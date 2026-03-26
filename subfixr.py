import argparse
import glob
import os
import re
import shutil
import subprocess
import tempfile


TEXT_ENCODINGS = ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1', 'iso-8859-1']
TIME_ROW = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$')
BLOCK_NO = re.compile(r'^\d+$')
TIME_BITS = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})')
SRT_GLOB = '*.[sS][rR][tT]'
MKS_GLOB = '*.[mM][kK][sS]'

LANG_MAP = {
    'en': 'eng', 'es': 'spa', 'id': 'ind', 'ko': 'kor', 'ms': 'may',
    'pt-br': 'pt-br', 'pt': 'por', 'th': 'tha', 'vi': 'vie', 'zh': 'zho',
    'fr': 'fre', 'de': 'ger', 'it': 'ita', 'ja': 'jpn', 'ru': 'rus',
    'ar': 'ara', 'hi': 'hin', 'tr': 'tur', 'pl': 'pol', 'nl': 'dut',
    'sv': 'swe', 'da': 'dan', 'no': 'nor', 'fi': 'fin', 'cs': 'cze',
    'hu': 'hun', 'ro': 'rum', 'el': 'gre', 'he': 'heb', 'uk': 'ukr',
    'bg': 'bul', 'hr': 'hrv', 'sk': 'slo', 'sl': 'slv', 'sr': 'srp',
    'mk': 'mac', 'sq': 'alb', 'et': 'est', 'lv': 'lav', 'lt': 'lit',
    'is': 'ice', 'ga': 'gle', 'cy': 'wel', 'mt': 'mlt', 'eu': 'baq',
    'ca': 'cat', 'gl': 'glg', 'oc': 'oci', 'sc': 'srd', 'co': 'cos',
    'br': 'bre', 'gd': 'gla', 'fo': 'fao', 'rm': 'roh', 'wa': 'wln',
    'lb': 'ltz', 'af': 'afr', 'sw': 'swa', 'zu': 'zul', 'xh': 'xho',
    'tn': 'tsn', 'st': 'sot', 'ss': 'ssw', 've': 'ven', 'ts': 'tso',
    'nr': 'nbl', 'nso': 'nso'
}


def run_text_command(args, timeout):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding='ascii',
        errors='backslashreplace',
        timeout=timeout,
    )


def ms_from_timecode(raw):
    hit = TIME_BITS.match(raw)
    if not hit:
        raise ValueError(f"Invalid time format: {raw}")
    hh, mm, ss, ms = (int(part) for part in hit.groups())
    return hh * 3600000 + mm * 60000 + ss * 1000 + ms


def timecode_from_ms(total_ms):
    if total_ms < 0:
        total_ms = 0
    hh, rem = divmod(total_ms, 3600000)
    mm, rem = divmod(rem, 60000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def shift_time_row(row, delta):
    hit = re.match(r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})$', row)
    if not hit:
        return row
    start, end = hit.groups()
    moved_start = timecode_from_ms(ms_from_timecode(start) + delta)
    moved_end = timecode_from_ms(ms_from_timecode(end) + delta)
    return f"{moved_start} --> {moved_end}"


def parse_shift(raw):
    raw = raw.strip().lower()
    if raw.endswith('min'):
        return round(float(raw[:-3]) * 60000)
    if raw.endswith('ms'):
        return round(float(raw[:-2]))
    if raw.endswith('s'):
        return round(float(raw[:-1]) * 1000)
    try:
        return round(float(raw) * 1000)
    except ValueError as exc:
        raise ValueError(f"Invalid time format: {raw}. Use something like 1s, 1ms, 1min, or plain seconds") from exc


def clean_direction(raw):
    raw = raw.lower().strip()
    if raw in ('for', 'forward', 'fwd', 'f'):
        return 'forward'
    if raw in ('back', 'backward', 'bwd', 'b'):
        return 'backward'
    raise ValueError(f"Invalid direction: {raw}. Use forward/for or backward/back")


def load_text(path, limit=None):
    last_error = None
    for enc in TEXT_ENCODINGS:
        try:
            with open(path, 'r', encoding=enc, newline='') as fh:
                text = fh.read() if limit is None else fh.read(limit)
            newline = '\n'
            if '\r\n' in text:
                newline = '\r\n'
            elif '\r' in text:
                newline = '\r'
            return text, newline
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"Could not decode file with common encodings: {', '.join(TEXT_ENCODINGS)}") from last_error


def read_blocks(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return []

    chunks = re.split(r'\n\s*\n', text)
    blocks = []
    for chunk in chunks:
        lines = chunk.split('\n')
        if len(lines) < 2:
            continue

        maybe_no = lines[0].strip()
        when = lines[1].strip()
        if not BLOCK_NO.match(maybe_no):
            continue
        if not TIME_ROW.match(when):
            continue

        blocks.append({
            'number': int(maybe_no),
            'timestamp': lines[1],
            'text': lines[2:],
        })
    return blocks


def join_blocks(blocks):
    out = []
    idx = 1
    for block in blocks:
        out.append(str(idx))
        out.append(block['timestamp'])
        out.extend(block['text'])
        out.append('')
        idx += 1
    return '\n'.join(out)


def parse_block_selector(raw):
    picks = set()
    pieces = [piece.strip() for piece in raw.split(',') if piece.strip()]
    if not pieces:
        raise ValueError("Invalid --delete-lines value: use subtitle numbers or ranges like 1-8 or 1,3,5-7")

    for piece in pieces:
        if '-' not in piece:
            if not piece.isdigit():
                raise ValueError(f"Invalid --delete-lines entry: {piece}")
            number = int(piece)
            if number < 1:
                raise ValueError(f"Invalid --delete-lines entry: {piece}. Subtitle numbers must start from 1")
            picks.add(number)
            continue

        left, right = [part.strip() for part in piece.split('-', 1)]
        if not left.isdigit() or not right.isdigit():
            raise ValueError(f"Invalid --delete-lines range: {piece}")
        start = int(left)
        end = int(right)
        if start < 1 or end < 1:
            raise ValueError(f"Invalid --delete-lines range: {piece}. Subtitle numbers must start from 1")
        if start > end:
            raise ValueError(f"Invalid --delete-lines range: {piece}. Start must be less than or equal to end")
        picks.update(range(start, end + 1))

    return picks


def drop_blocks_by_number(text, spec):
    doomed = parse_block_selector(spec)
    blocks = read_blocks(text)
    keep = [block for block in blocks if block['number'] not in doomed]
    return join_blocks(keep), len(blocks) - len(keep)


def drop_blocks_by_text(text, needle):
    blocks = read_blocks(text)
    keep = []
    hit_count = 0
    needle = needle.lower()

    for block in blocks:
        blob = ' '.join(block['text']).lower()
        if needle in blob:
            hit_count += 1
            continue
        keep.append(block)

    return join_blocks(keep), hit_count


def fix_one(src, dst, shift_value, direction, remove_text=None, delete_lines=None):
    delta = 0
    if shift_value:
        if not direction:
            raise ValueError("Direction is required when shift value is provided")
        delta = parse_shift(shift_value)
        if direction == 'backward':
            delta = -delta

    text, line_ending = load_text(src)

    if delete_lines:
        text, removed = drop_blocks_by_number(text, delete_lines)
        print(f"    Removed {removed} subtitle block(s) from '{delete_lines}'")

    if remove_text:
        text, removed = drop_blocks_by_text(text, remove_text)
        print(f"    Removed {removed} subtitle block(s) containing '{remove_text}'")

    out = []
    moved = 0
    for row in text.splitlines():
        if delta and TIME_ROW.match(row):
            out.append(shift_time_row(row, delta))
            moved += 1
        else:
            out.append(row)

    folder = os.path.dirname(dst)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(dst, 'w', encoding='utf-8', newline='') as fh:
        fh.write(line_ending.join(out))

    print(f"[+] Processed: {os.path.basename(src)}")
    if delta:
        print(f"    Shift: {direction} by {shift_value} ({moved} timestamps shifted)")
    print(f"    Output: {dst}")


def lang_as_iso639_2(code):
    code = code.replace('_', '-').lower()
    if code in LANG_MAP:
        return LANG_MAP[code]
    if '-' not in code:
        return LANG_MAP.get(code, code)
    base, region = code.split('-', 1)
    return f"{LANG_MAP.get(base, base)}-{region}"


def tweak_lang_in_stem(stem):
    folder = os.path.dirname(stem)
    name = os.path.basename(stem)

    bits = name.rsplit('.', 1)
    if len(bits) == 2:
        base, maybe_lang = bits
        swapped = lang_as_iso639_2(maybe_lang)
        if swapped != maybe_lang:
            name = f"{base}.{swapped}"

    return os.path.join(folder, name) if folder else name


def tweak_lang_in_filename(path):
    folder = os.path.dirname(path)
    filename = os.path.basename(path)
    stem, ext = os.path.splitext(filename)
    renamed = os.path.basename(tweak_lang_in_stem(stem)) + ext
    return os.path.join(folder, renamed) if folder else renamed


def have_mkv_tools():
    for cmd in ('mkvextract', 'mkvinfo'):
        try:
            result = run_text_command([cmd, '--version'], timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        if result.returncode != 0:
            return False
    return True


def pull_tracks_from_mks(mks_file, temp_dir):
    if not have_mkv_tools():
        raise RuntimeError("mkvextract/mkvinfo not found. Please install MKVToolNix")

    try:
        probe = run_text_command(['mkvinfo', mks_file], timeout=30)
        if probe.returncode != 0:
            msg = probe.stderr.strip() if probe.stderr else 'Unknown error'
            raise RuntimeError(f"mkvinfo failed: {msg}")

        tracks = []
        track_id = None
        lang = 'unknown'
        is_subtitle = False

        probe_output = '\n'.join(part for part in (probe.stdout, probe.stderr) if part)
        for row in probe_output.splitlines():
            row_lower = row.lower()
            hit = re.search(r'track id for mkvmerge.*?:\s*(\d+)', row, re.IGNORECASE)
            if hit:
                if is_subtitle and track_id is not None:
                    tracks.append((track_id, lang))
                track_id = int(hit.group(1))
                lang = 'unknown'
                is_subtitle = False

            if track_id is not None:
                if 'track type: subtitles' in row_lower or 'track type: text' in row_lower:
                    is_subtitle = True

                if is_subtitle and 'language' in row_lower:
                    ietf = re.search(r'language\s*\([^)]*ietf[^)]*\)[:\s]+([\w-]+)', row_lower)
                    if ietf:
                        lang = ietf.group(1).lower()
                    elif lang == 'unknown':
                        plain = re.search(r'language[:\s]+([\w-]+)', row_lower)
                        if plain:
                            lang = plain.group(1).lower()

            if re.match(r'\s*\|\s+\+ Track\s*$', row):
                if is_subtitle and track_id is not None:
                    tracks.append((track_id, lang))
                track_id = None
                lang = 'unknown'
                is_subtitle = False

        if is_subtitle and track_id is not None:
            tracks.append((track_id, lang))

        if not tracks:
            for guess in range(10):
                scratch = os.path.join(temp_dir, f"test_track{guess}.srt")
                test = run_text_command(['mkvextract', 'tracks', mks_file, f'{guess}:{scratch}'], timeout=10)
                if test.returncode != 0 or not os.path.exists(scratch):
                    continue

                try:
                    head, _ = load_text(scratch, limit=200)
                except ValueError:
                    head = ''

                if '-->' in head:
                    tracks.append((guess, 'unknown'))

                if os.path.exists(scratch):
                    try:
                        os.remove(scratch)
                    except OSError:
                        pass

        if not tracks:
            raise ValueError("No subtitle tracks found in MKS file. Try: mkvextract tracks file.mks 1:output.srt")

        base = os.path.splitext(os.path.basename(mks_file))[0]
        extracted = []
        for track_id, lang in tracks:
            lang_code = lang_as_iso639_2(lang) if lang != 'unknown' else lang
            out_file = os.path.join(temp_dir, f"{base}_track{track_id}.{lang_code}.srt")

            result = run_text_command(['mkvextract', 'tracks', mks_file, f'{track_id}:{out_file}'], timeout=60)

            if result.returncode != 0 or not os.path.exists(out_file):
                msg = result.stderr.strip() if result.stderr else result.stdout.strip()
                if msg:
                    print(f"    Warning: Failed to extract track {track_id} ({lang_code}): {msg}")
                continue

            try:
                text, _ = load_text(out_file)
            except ValueError:
                text = ''

            if text and text.strip():
                extracted.append((out_file, track_id, lang_code))
                continue

            if os.path.exists(out_file):
                try:
                    os.remove(out_file)
                except OSError:
                    pass

        return extracted

    except subprocess.TimeoutExpired:
        raise RuntimeError("mkvextract operation timed out")
    except FileNotFoundError:
        raise RuntimeError("mkvextract or mkvinfo not found. Please install MKVToolNix from https://mkvtoolnix.download/")
    except Exception as exc:
        raise RuntimeError(f"Error extracting subtitles from MKS: {exc}")


def scan_inputs(input_path, recursive=False, include_mks=False):
    srt_files = []
    mks_files = []

    if os.path.isfile(input_path):
        lower = input_path.lower()
        if lower.endswith('.srt'):
            return [input_path], []
        if include_mks and lower.endswith('.mks'):
            return [], [input_path]
        raise ValueError(f"Input file is not a supported format (.srt or .mks): {input_path}")

    if not os.path.isdir(input_path):
        raise FileNotFoundError(f"Input path not found: {input_path}")

    if recursive:
        srt_files = glob.glob(os.path.join(input_path, '**', SRT_GLOB), recursive=True)
        if include_mks:
            mks_files = glob.glob(os.path.join(input_path, '**', MKS_GLOB), recursive=True)
    else:
        srt_files = glob.glob(os.path.join(input_path, SRT_GLOB))
        if include_mks:
            mks_files = glob.glob(os.path.join(input_path, MKS_GLOB))

    if not srt_files and not mks_files:
        raise ValueError(f"No subtitle files (.srt or .mks) found in: {input_path}")

    srt_files.sort()
    mks_files.sort()
    return srt_files, mks_files


def process_path(input_path, output_path, shift_value, direction, recursive=False, overwrite=False, remove_text=None, delete_lines=None):
    srt_files, mks_files = scan_inputs(input_path, recursive, include_mks=True)

    folder_mode = os.path.isdir(input_path)
    out_path = os.path.normpath(output_path) if output_path else None
    looks_like_dir = False
    if output_path:
        seps = [os.sep]
        if os.altsep:
            seps.append(os.altsep)
        looks_like_dir = any(output_path.endswith(sep) for sep in seps)

    temp_dir = None
    temp_home = {}
    if mks_files:
        temp_dir = tempfile.mkdtemp(prefix='subfixr_')
        print(f"[+] Extracting subtitles from {len(mks_files)} MKS file(s)...")

        for mks_file in mks_files:
            try:
                extracted = pull_tracks_from_mks(mks_file, temp_dir)
            except Exception as exc:
                print(f"    Error processing {os.path.basename(mks_file)}: {exc}")
                continue

            home = os.path.dirname(os.path.abspath(mks_file)) or '.'
            for extracted_file, track_id, lang in extracted:
                temp_home[extracted_file] = home
                srt_files.append(extracted_file)
            print(f"    Extracted {len(extracted)} track(s) from {os.path.basename(mks_file)}")

        print()

    out_is_dir = False
    if out_path:
        out_is_dir = folder_mode or looks_like_dir or os.path.isdir(out_path)
        if out_is_dir and os.path.exists(out_path) and os.path.isfile(out_path):
            raise ValueError(f"Output path exists as a file, but a folder is required: {out_path}")
        if out_is_dir and not os.path.exists(out_path):
            os.makedirs(out_path, exist_ok=True)
            print(f"[+] Created output folder: {out_path}\n")

    print(f"[+] Found {len(srt_files)} subtitle file(s)")
    if shift_value:
        print(f"[+] Shift: {direction} by {shift_value}")
    if delete_lines:
        print(f"[+] Delete subtitle blocks: {delete_lines}")
    if remove_text:
        print(f"[+] Remove lines containing: {remove_text}")
    print()

    done = 0
    skipped = 0

    try:
        for src in srt_files:
            from_temp = False
            if temp_dir:
                try:
                    shared = os.path.commonpath([os.path.abspath(temp_dir), os.path.abspath(src)])
                    from_temp = os.path.abspath(shared) == os.path.abspath(temp_dir)
                except (ValueError, OSError):
                    from_temp = False

            if folder_mode or from_temp:
                if out_path:
                    if out_is_dir:
                        if from_temp:
                            stem = os.path.splitext(os.path.basename(src))[0]
                            stem = tweak_lang_in_stem(stem)
                            dst = os.path.join(out_path, f"{os.path.basename(stem)}.srt")
                        else:
                            dst = os.path.join(out_path, tweak_lang_in_filename(os.path.basename(src)))
                    else:
                        if from_temp:
                            stem = os.path.splitext(os.path.basename(src))[0]
                            stem = tweak_lang_in_stem(stem)
                            base_out, ext = os.path.splitext(out_path)
                            if not ext:
                                ext = '.srt'
                            if '_track' in stem:
                                dst = f"{base_out}_track{stem.split('_track', 1)[1]}{ext}"
                            else:
                                dst = f"{base_out}_{os.path.basename(stem)}{ext}"
                        else:
                            dst = out_path
                else:
                    if from_temp:
                        home = temp_home.get(src, '.')
                        stem = os.path.splitext(os.path.basename(src))[0]
                        stem = tweak_lang_in_stem(stem)
                        if '_track' in stem:
                            base_name, tail = stem.split('_track', 1)
                            dst = os.path.join(home, f"{base_name}_track{tail}.srt")
                        else:
                            dst = os.path.join(home, f"{stem}_synced.srt")
                    else:
                        base, ext = os.path.splitext(src)
                        dst = f"{tweak_lang_in_stem(base)}_synced{ext}"
            else:
                if out_path:
                    if out_is_dir:
                        dst = os.path.join(out_path, tweak_lang_in_filename(os.path.basename(src)))
                    else:
                        dst = out_path
                else:
                    base, ext = os.path.splitext(src)
                    dst = f"{tweak_lang_in_stem(base)}_synced{ext}"

            if os.path.exists(dst) and not overwrite:
                print(f"[-] Skipped: {os.path.basename(src)} (output exists, use --overwrite)")
                skipped += 1
                continue

            try:
                fix_one(src, dst, shift_value, direction, remove_text, delete_lines)
                done += 1
            except Exception as exc:
                print(f"[-] Error processing {os.path.basename(src)}: {exc}")
                skipped += 1

            print()
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"[+] Summary: {done} processed, {skipped} skipped")


def main():
    parser = argparse.ArgumentParser(
        prog='SubFixr',
        description='Shift subtitle timing, remove specific subtitle blocks, or drop blocks that match some text',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python subfixr.py file.srt -s 1s -d for
  python subfixr.py folder/ -s 500ms -d back
  python subfixr.py folder/ -s 1min -d forward -o output_folder/ -r
  python subfixr.py temp/ -s 2.5s -d backward --overwrite
  python subfixr.py file.srt --delete-lines 1-8
  python subfixr.py file.srt --delete-lines 1,3,10-15 -s 750ms -d back
  python subfixr.py file.srt --remove "viki"
  python subfixr.py folder/ --remove "viki.com" -s 1s -d for
  python subfixr.py file.mks -s 1s -d for -o output_folder/

If you combine options, they run in this order:
  1. --delete-lines
  2. --remove
  3. timestamp shift
        """
    )
    parser.add_argument('input', help='Subtitle file (.srt or .mks) or a folder with subtitle files')
    parser.add_argument('-o', '--output', default=None, help='Where to write the result. Defaults to input_synced.srt or the source folder')
    parser.add_argument('-s', '--shift', default=None, help='How much to shift timestamps, for example 1s, 500ms, 1min, or plain seconds like 1.5')
    parser.add_argument('-d', '--direction', default='forward', help='Which way to shift: forward/for or backward/back')
    parser.add_argument('--delete-lines', default=None, help='Subtitle block numbers or ranges to remove, for example 1-8 or 1,3,5-7')
    parser.add_argument('--remove', default=None, help='Remove subtitle blocks that contain this text (case-insensitive)')
    parser.add_argument('-r', '--recursive', action='store_true', help='Scan subfolders too when the input is a folder')
    parser.add_argument('--overwrite', action='store_true', help='Replace output files if they already exist')

    args = parser.parse_args()

    if not args.shift and not args.remove and not args.delete_lines:
        parser.error('Nothing to do. Pass at least one of --shift, --remove, or --delete-lines')

    direction = None
    if args.shift:
        try:
            direction = clean_direction(args.direction)
        except ValueError as exc:
            parser.error(str(exc))

    if args.delete_lines:
        try:
            parse_block_selector(args.delete_lines)
        except ValueError as exc:
            parser.error(str(exc))

    process_path(
        args.input,
        args.output,
        args.shift,
        direction,
        args.recursive,
        args.overwrite,
        args.remove,
        args.delete_lines,
    )


if __name__ == '__main__':
    main()
