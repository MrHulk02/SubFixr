import argparse
import glob
import json
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
SUBRIP_CODECS = {'S_TEXT/UTF8', 'S_TEXT/ASCII'}

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


def run_text_command(args, timeout, encoding='ascii', errors='backslashreplace'):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors=errors,
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


def render_fixed_subtitle(src, shift_value, direction, remove_text=None, delete_lines=None):
    delta = 0
    if shift_value:
        if not direction:
            raise ValueError("Direction is required when shift value is provided")
        delta = parse_shift(shift_value)
        if direction == 'backward':
            delta = -delta

    text, line_ending = load_text(src)
    removed_by_number = 0
    removed_by_text = 0

    if delete_lines:
        text, removed_by_number = drop_blocks_by_number(text, delete_lines)

    if remove_text:
        text, removed_by_text = drop_blocks_by_text(text, remove_text)

    out = []
    moved = 0
    for row in text.splitlines():
        if delta and TIME_ROW.match(row):
            out.append(shift_time_row(row, delta))
            moved += 1
        else:
            out.append(row)

    return line_ending.join(out), {
        'delta': delta,
        'removed_by_number': removed_by_number,
        'removed_by_text': removed_by_text,
        'moved': moved,
    }


def write_text(path, text):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)


def fix_one(src, dst, shift_value, direction, remove_text=None, delete_lines=None):
    rendered, stats = render_fixed_subtitle(src, shift_value, direction, remove_text, delete_lines)

    if delete_lines:
        print(f"    Removed {stats['removed_by_number']} subtitle block(s) from '{delete_lines}'")

    if remove_text:
        print(f"    Removed {stats['removed_by_text']} subtitle block(s) containing '{remove_text}'")

    write_text(dst, rendered)

    print(f"[+] Processed: {os.path.basename(src)}")
    if stats['delta']:
        print(f"    Shift: {direction} by {shift_value} ({stats['moved']} timestamps shifted)")
    print(f"    Output: {dst}")
    return stats


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
    for cmd in ('mkvextract', 'mkvmerge'):
        try:
            result = run_text_command([cmd, '--version'], timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        if result.returncode != 0:
            return False
    return True


def inspect_mks(mks_file):
    result = run_text_command(
        ['mkvmerge', '--identify', '--identification-format', 'json', mks_file],
        timeout=30,
        encoding='utf-8',
        errors='replace',
    )
    if result.returncode != 0:
        msg = result.stderr.strip() if result.stderr else 'Unknown error'
        raise RuntimeError(f"mkvmerge identify failed: {msg}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse mkvmerge output for {mks_file}") from exc


def track_lang_code(track):
    lang = track.get('language_ietf') or track.get('language') or 'unknown'
    return lang_as_iso639_2(lang) if lang != 'unknown' else lang


def track_label(track):
    label = track.get('language_ietf') or track.get('language') or 'unknown'
    if track.get('track_name'):
        return f"{label} | {track['track_name']}"
    return label


def bool_flag(value):
    return '1' if value else '0'


def pull_tracks_from_mks(mks_file, temp_dir, strict=False):
    if not have_mkv_tools():
        raise RuntimeError("mkvextract/mkvmerge not found. Please install MKVToolNix")

    try:
        info = inspect_mks(mks_file)
        title = info.get('container', {}).get('properties', {}).get('title')
        tracks = []
        editable_count = 0

        for raw_track in info.get('tracks', []):
            if raw_track.get('type') != 'subtitles':
                continue

            props = raw_track.get('properties', {})
            track = {
                'id': raw_track.get('id'),
                'number': props.get('number'),
                'codec': raw_track.get('codec'),
                'codec_id': props.get('codec_id'),
                'language': props.get('language'),
                'language_ietf': props.get('language_ietf'),
                'track_name': props.get('track_name'),
                'default_track': props.get('default_track', False),
                'forced_track': props.get('forced_track', False),
                'enabled_track': props.get('enabled_track', True),
                'flag_hearing_impaired': props.get('flag_hearing_impaired', False),
                'flag_original': props.get('flag_original', False),
            }

            if track['codec_id'] not in SUBRIP_CODECS:
                tracks.append(track)
                continue

            lang_code = track_lang_code(track)
            base = os.path.splitext(os.path.basename(mks_file))[0]
            out_file = os.path.join(temp_dir, f"{base}_track{track['id']}.{lang_code}.srt")
            result = run_text_command(['mkvextract', 'tracks', mks_file, f"{track['id']}:{out_file}"], timeout=60)

            if result.returncode != 0 or not os.path.exists(out_file):
                msg = result.stderr.strip() if result.stderr else result.stdout.strip()
                if strict:
                    raise RuntimeError(f"Failed to extract track {track['id']} ({lang_code}): {msg or 'Unknown error'}")
                if msg:
                    print(f"    Warning: Failed to extract track {track['id']} ({lang_code}): {msg}")
                tracks.append(track)
                continue

            try:
                text, _ = load_text(out_file)
            except ValueError:
                text = ''

            if text and text.strip():
                track['extracted_path'] = out_file
                editable_count += 1

            tracks.append(track)

        if not tracks:
            raise ValueError("No subtitle tracks found in MKS file.")

        if editable_count == 0:
            raise ValueError("No supported SubRip/SRT subtitle tracks found in MKS file.")

        return {
            'source': mks_file,
            'title': title,
            'tracks': tracks,
        }

    except subprocess.TimeoutExpired:
        raise RuntimeError("mkvextract operation timed out")
    except FileNotFoundError:
        raise RuntimeError("mkvextract or mkvmerge not found. Please install MKVToolNix from https://mkvtoolnix.download/")
    except Exception as exc:
        raise RuntimeError(f"Error extracting subtitles from MKS: {exc}")


def make_temp_workspace(temp_root):
    return tempfile.mkdtemp(prefix='mks_', dir=temp_root)


def resolve_mks_output_path(src, out_path, out_is_dir, folder_mode):
    if out_path:
        if out_is_dir:
            return os.path.join(out_path, os.path.basename(src))

        if folder_mode:
            base_out, ext = os.path.splitext(out_path)
            if not ext:
                ext = '.mks'
            stem = os.path.splitext(os.path.basename(src))[0]
            return f"{base_out}_{stem}{ext}"

        base_out, ext = os.path.splitext(out_path)
        return out_path if ext else f"{base_out}.mks"

    base, _ = os.path.splitext(src)
    return f"{base}_synced.mks"


def mks_track_args(track):
    args = ['--sub-charset', '0:utf-8']

    language = track.get('language_ietf') or track.get('language')
    if language:
        args.extend(['--language', f'0:{language}'])

    if track.get('track_name') is not None:
        args.extend(['--track-name', f"0:{track['track_name']}"])

    args.extend(['--default-track-flag', f"0:{bool_flag(track.get('default_track', False))}"])
    args.extend(['--forced-display-flag', f"0:{bool_flag(track.get('forced_track', False))}"])
    args.extend(['--track-enabled-flag', f"0:{bool_flag(track.get('enabled_track', True))}"])
    args.extend(['--hearing-impaired-flag', f"0:{bool_flag(track.get('flag_hearing_impaired', False))}"])
    args.extend(['--original-flag', f"0:{bool_flag(track.get('flag_original', False))}"])
    return args


def remux_mks(job, dst):
    folder = os.path.dirname(dst)
    if folder:
        os.makedirs(folder, exist_ok=True)

    cmd = ['mkvmerge', '-o', dst]
    if job.get('title'):
        cmd.extend(['--title', job['title']])

    passthrough_ids = [str(track['id']) for track in job['tracks'] if not track.get('processed_path')]
    if passthrough_ids:
        cmd.extend(['--subtitle-tracks', ','.join(passthrough_ids), job['source']])
    else:
        cmd.extend(['--no-subtitles', job['source']])

    track_refs = {}
    next_input_id = 1
    for track in job['tracks']:
        if track.get('processed_path'):
            cmd.extend(mks_track_args(track))
            cmd.append(track['processed_path'])
            track_refs[track['id']] = f'{next_input_id}:0'
            next_input_id += 1
        else:
            track_refs[track['id']] = f"0:{track['id']}"

    cmd.extend(['--track-order', ','.join(track_refs[track['id']] for track in job['tracks'])])
    result = run_text_command(cmd, timeout=120, encoding='utf-8', errors='replace')
    if result.returncode != 0 or not os.path.exists(dst):
        msg = result.stderr.strip() if result.stderr else result.stdout.strip()
        raise RuntimeError(f"mkvmerge failed: {msg or 'Unknown error'}")


def process_mks_file(job, dst, shift_value, direction, remove_text=None, delete_lines=None):
    print(f"[+] Rebuilding: {os.path.basename(job['source'])}")
    passthrough = 0
    for track in job['tracks']:
        extracted = track.get('extracted_path')
        if not extracted:
            passthrough += 1
            continue

        edited_path = os.path.join(os.path.dirname(extracted), f"edited_track{track['id']}.srt")
        rendered, stats = render_fixed_subtitle(extracted, shift_value, direction, remove_text, delete_lines)
        write_text(edited_path, rendered)
        track['processed_path'] = edited_path

        print(f"    Track {track['id']} ({track_label(track)})")
        if delete_lines:
            print(f"      Removed {stats['removed_by_number']} subtitle block(s) from '{delete_lines}'")
        if remove_text:
            print(f"      Removed {stats['removed_by_text']} subtitle block(s) containing '{remove_text}'")
        if stats['delta']:
            print(f"      Shift: {direction} by {shift_value} ({stats['moved']} timestamps shifted)")

    if passthrough:
        print(f"    Preserved {passthrough} original subtitle track(s) without edits")

    remux_mks(job, dst)
    print(f"[+] Processed: {os.path.basename(job['source'])}")
    print(f"    Output: {dst}")


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


def process_path(input_path, output_path, shift_value, direction, recursive=False, overwrite=False, remove_text=None, delete_lines=None, mks_output=False):
    srt_files, mks_files = scan_inputs(input_path, recursive, include_mks=True)

    folder_mode = os.path.isdir(input_path)
    out_path = os.path.normpath(output_path) if output_path else None
    looks_like_dir = False
    if output_path:
        seps = [os.sep]
        if os.altsep:
            seps.append(os.altsep)
        looks_like_dir = any(output_path.endswith(sep) for sep in seps)

    temp_dir = tempfile.mkdtemp(prefix='subfixr_') if mks_files else None
    temp_home = {}
    if mks_files and not mks_output:
        print(f"[+] Extracting subtitles from {len(mks_files)} MKS file(s)...")

        for mks_file in mks_files:
            work_dir = make_temp_workspace(temp_dir)
            try:
                job = pull_tracks_from_mks(mks_file, work_dir)
            except Exception as exc:
                print(f"    Error processing {os.path.basename(mks_file)}: {exc}")
                continue

            home = os.path.dirname(os.path.abspath(mks_file)) or '.'
            extracted = 0
            skipped_tracks = 0
            for track in job['tracks']:
                extracted_file = track.get('extracted_path')
                if not extracted_file:
                    skipped_tracks += 1
                    continue

                temp_home[extracted_file] = home
                srt_files.append(extracted_file)
                extracted += 1
            print(f"    Extracted {extracted} track(s) from {os.path.basename(mks_file)}")
            if skipped_tracks:
                print(f"    Skipped {skipped_tracks} subtitle track(s) that are not editable as SRT")

        print()

    out_is_dir = False
    if out_path:
        out_is_dir = folder_mode or looks_like_dir or os.path.isdir(out_path)
        if out_is_dir and os.path.exists(out_path) and os.path.isfile(out_path):
            raise ValueError(f"Output path exists as a file, but a folder is required: {out_path}")
        if out_is_dir and not os.path.exists(out_path):
            os.makedirs(out_path, exist_ok=True)
            print(f"[+] Created output folder: {out_path}\n")

    if mks_output:
        print(f"[+] Found {len(srt_files)} SRT file(s)")
        print(f"[+] Found {len(mks_files)} MKS file(s)")
    else:
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

        if mks_output:
            for src in mks_files:
                dst = resolve_mks_output_path(src, out_path, out_is_dir, folder_mode)

                if os.path.exists(dst) and not overwrite:
                    print(f"[-] Skipped: {os.path.basename(src)} (output exists, use --overwrite)")
                    skipped += 1
                    print()
                    continue

                if os.path.exists(dst) and overwrite:
                    try:
                        os.remove(dst)
                    except OSError as exc:
                        print(f"[-] Error processing {os.path.basename(src)}: Could not replace existing output: {exc}")
                        skipped += 1
                        print()
                        continue

                work_dir = make_temp_workspace(temp_dir)
                try:
                    job = pull_tracks_from_mks(src, work_dir, strict=True)
                    process_mks_file(job, dst, shift_value, direction, remove_text, delete_lines)
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
  python subfixr.py file.mks --remove "viki" --mks-output

If you combine options, they run in this order:
  1. --delete-lines
  2. --remove
  3. timestamp shift
        """
    )
    parser.add_argument('input', help='Subtitle file (.srt or .mks) or a folder with subtitle files')
    parser.add_argument('-o', '--output', default=None, help='Where to write the result. Defaults to input_synced.srt/.mks or the source folder')
    parser.add_argument('-s', '--shift', default=None, help='How much to shift timestamps, for example 1s, 500ms, 1min, or plain seconds like 1.5')
    parser.add_argument('-d', '--direction', default='forward', help='Which way to shift: forward/for or backward/back')
    parser.add_argument('--delete-lines', default=None, help='Subtitle block numbers or ranges to remove, for example 1-8 or 1,3,5-7')
    parser.add_argument('--remove', default=None, help='Remove subtitle blocks that contain this text (case-insensitive)')
    parser.add_argument('-r', '--recursive', action='store_true', help='Scan subfolders too when the input is a folder')
    parser.add_argument('--overwrite', action='store_true', help='Replace output files if they already exist')
    parser.add_argument('--mks-output', action='store_true', help='When the input is .mks, rebuild a new .mks instead of writing extracted .srt tracks')

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
        args.mks_output,
    )


if __name__ == '__main__':
    main()
