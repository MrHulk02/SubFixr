# SubFixr

SubFixr is a small command-line tool for fixing and cleaning subtitle files.

It’s mainly for those moments when subtitles are slightly out of sync, or when you want to remove things like watermarks, ads, or unwanted lines quickly without opening a full editor.

It can shift SRT timestamps forward or backward, remove subtitle blocks by number, and drop blocks that contain matching text. If you have MKVToolNix installed, it can also extract subtitle tracks from `.mks` files and process them the same way.

---

## Requirements

* Python 3
* MKVToolNix on your `PATH` (only needed for `.mks` support)

---

## What it does

* Shift subtitle timing forward or backward
* Remove subtitle blocks by number or range
* Remove subtitle blocks that contain specific text
* Process a single file or a whole folder
* Walk subfolders with `--recursive`
* Write output next to the source file or to a custom path

---

## Usage

```bash
python subfixr.py input [options]
```

You need to pass at least one of these:

* `--shift`
* `--remove`
* `--delete-lines`

---

## Main options

* `input`
  Subtitle file (`.srt` or `.mks`) or a folder with subtitle files

* `-s, --shift`
  Shift amount like `1s`, `500ms`, `1min`, or plain seconds like `1.5`

* `-d, --direction`
  `forward` / `for` or `backward` / `back`

* `--delete-lines`
  Subtitle block numbers or ranges like `1-8` or `1,3,5-7`

* `--remove`
  Remove subtitle blocks that contain the given text

* `-o, --output`
  Output file or folder

* `-r, --recursive`
  Scan subfolders too when the input is a folder

* `--overwrite`
  Replace output files if they already exist

---

## Examples

Shift one file forward by one second:

```bash
python subfixr.py file.srt -s 1s -d for
```

Shift every subtitle in a folder back by 500 ms:

```bash
python subfixr.py folder/ -s 500ms -d back
```

Process a folder recursively and write everything to another folder:

```bash
python subfixr.py folder/ -s 1min -d forward -o output_folder/ -r
```

Overwrite files if the destination already exists:

```bash
python subfixr.py temp/ -s 2.5s -d backward --overwrite
```

Remove subtitle blocks by number:

```bash
python subfixr.py file.srt --delete-lines 1-8
```

Remove a few blocks, then shift the rest:

```bash
python subfixr.py file.srt --delete-lines 1,3,10-15 -s 750ms -d back
```

Remove blocks that contain some text:

```bash
python subfixr.py file.srt --remove "viki"
```

Process a folder and remove matching text before shifting:

```bash
python subfixr.py folder/ --remove "viki.com" -s 1s -d for
```

Extract subtitle tracks from an `.mks` file and write them to a folder:

```bash
python subfixr.py file.mks -s 1s -d for -o output_folder/
```

---

## Order of operations

If you combine options, SubFixr runs them in this order:

1. `--delete-lines`
2. `--remove`
3. timestamp shift

---

## Notes

* Output defaults to a new file next to the source (usually with `_synced` added to the name)
* `.mks` support depends on `mkvextract` and `mkvinfo` being available on your system

---

## Small tip

If you're dealing with a lot of files, try running it on a folder with `--recursive` first and without `--overwrite`, just to see what gets generated. Once you're happy, run it again with overwrite enabled.
