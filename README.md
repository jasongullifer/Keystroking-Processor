# XML TO VIDEO - USER GUIDE

This program converts keystroke/typing data from XML, Data TXT, or IDFX files
into video animations that replay the typing process on a blank background.

**Requirements:**
This guide will cover the installation of the Python packages, but not Python itself.
- **Python 3.8** or newer                  
- **Packages (from `requirements.txt`):** moviepy, Pillow, lxml, python-docx, ijson

---

## INPUT AND OUTPUT
The program accepts and processes the following input files. It delivers the following output files.

### INPUT

**XML/Word:** XML files containing keyboard event data (e.g. from Microsoft Word
typing activity), optionally paired with a `.docx` Word file for uniform
typing mode.

**Data TXT:** JSON-format `data.txt` files containing keyboard response events
and timestamps (often from typing studies or research tools).

**IDFX:** TypingDNA IDFX format files (XML-based) containing keyboard events
with system key codes.

### OUTPUT

- MP4 video files (1280x720 resolution, 20 fps)
- Videos show text appearing character-by-character with a blinking caret
- Output is saved as MP4 files

### OUTPUT LOCATION
The output files are stored in a folder generated upon the first process.
The folder is created in the same directory as the program (`keystroking_to_video.py`) and
is named: `xml-to-text-video-output`

- **Single file processing:** The video is saved directly in this folder.
- **Batch processing (2+ files):** A timestamped subfolder is created inside
  `xml-to-text-video-output`, e.g. `BATCH UPLOAD 2025-02-03 14-30-45`, and
  all batch outputs are placed inside it.

**Naming conventions:**
- XML files become `{filename}.mp4`
- Data TXT files become `{filename}_data.mp4`
- IDFX files become `{filename}_idfx.mp4`

Every generation also creates a CSV file with the exact settings used
(`{filename}_settings.csv`). Use these to keep a record or load them later
via "Load Settings from CSV" to save time when reusing the same setup.

---

## HOW TO INSTALL AND RUN THE PROGRAM
Before beginning, you should have Python 3.8 or newer installed. You can check your version by opening Terminal (Mac, Linux) or Command Prompt / PowerShell (Windows) and running:

- **Windows / Linux:** `python --version`
- **Mac:** `python3 --version`

If Python is not installed, download it from [python.org](https://www.python.org/downloads/) and install it before continuing.

> **Mac users:** On macOS, the command is usually `python3` instead of `python`. Wherever this guide shows `python`, use `python3` instead (e.g. `python3 -m venv myenv`, `python3 keystroking_to_video.py`).

### INSTALLATION

#### **Step 1: Download / Clone this repository**

Navigate to https://github.com/ZiruiZh/Keystroking-Processor click `Code` --> `Download ZIP`. Extract this zip file into a location of your choosing (e.g., `Documents` or `Desktop`).

Open Terminal (Mac, Linux), Command Prompt (Windows) or Powershell (Windows).

- Mac: Type `cd ` (with a space) in Terminal, then drag the folder from Finder into the Terminal window — it fills in the path automatically. Press `Enter`.

- Windows: Open the folder in File Explorer, then right-click inside it (Windows 11: "Open in Terminal"; Windows 10: "Open Terminal/PowerShell window here"). 

If you are using git, clone the repo instead and change directory to the program's folder:
```bash
git clone https://github.com/ZiruiZh/Keystroking-Processor.git
cd Keystroking-Processor
```
     
#### **Step 2: Create a virtual environment**

A virtual environment is a self-contained folder that keeps this project's dependencies separate from the rest of your system. It is strongly recommended, and avoids common "it worked on my computer" issues.

In Terminal/Command Prompt, while in the project's folder, run:

- **Windows / Linux:**
```bash
python -m venv myenv
```
- **Mac:**
```bash
python3 -m venv myenv
```
(You can replace `myenv` with any name you like, e.g. `venv`.)

#### **Step 3: Activate the virtual environment**
On macOS/Linux:
```bash
source myenv/bin/activate
```

On Windows (Command Prompt):
```powershell
myenv\Scripts\activate.bat
```

On Windows (PowerShell):
```powershell
myenv\Scripts\Activate.ps1
```
After activation, your prompt usually shows `(myenv)` or similar.

#### **Step 4: Install dependencies**
With the virtual environment active:
```bash
pip install -r requirements.txt
```

You are now ready to run the program!


### RUNNING THE PROGRAM

Each time you want to use the program, repeat these three steps:

**1. Open a terminal and navigate to the program's folder**

- **Mac:** Open Terminal. Type `cd ` (with a space), then drag the program folder from Finder into the Terminal window — it will fill in the path automatically. Press `Enter`.
- **Windows:** Open the program folder in File Explorer, then right-click inside it and choose "Open in Terminal" (Windows 11) or "Open PowerShell window here" (Windows 10).

**2. Activate the virtual environment**

You can tell it is active when you see `(myenv)` at the start of your terminal prompt.

On macOS/Linux:
```bash
source myenv/bin/activate
```

On Windows (Command Prompt):
```powershell
myenv\Scripts\activate.bat
```

On Windows (PowerShell):
```powershell
myenv\Scripts\Activate.ps1
```

**3. Run the program**

- **Windows / Linux:**
```bash
python keystroking_to_video.py
```
- **Mac:**
```bash
python3 keystroking_to_video.py
```

When you are finished, you can close the terminal or deactivate the virtual environment:
```bash
deactivate
```



## HOW TO USE - STEP BY STEP

### STEP 1: Choose Your File Type

At the top of the window, select from the dropdown: XML/Word, Data TXT,
or IDFX. The interface will switch to show the appropriate queue and options.
If the window is short or narrow, use the vertical and horizontal scrollbars,
mouse wheel, or laptop touchpad to reach all settings and buttons. Vertical
wheel or two-finger vertical scroll moves up/down; hold Shift while scrolling
(or use the horizontal scrollbar) to move left/right when the content is wider
than the window. On Linux, horizontal trackpads may also use mouse buttons 6/7.
Scrolling uses smooth pixel-based motion. Wheel or touchpad over list boxes and
text fields still scrolls those controls as usual.

### STEP 2: Add Files to the Queue

- Click "Add [type] to Queue"
- Select one or more input files
- For XML/Word: Each XML can optionally use a Word file; the last selected
  Word path applies to all XMLs in the queue unless you change it.
- Your queue is shown in the list below. Use "Clear Queue" to remove all.

### STEP 3: Configure Settings (Optional)

Adjust Text Settings, Moving Window, Uniform Typing Mode, and Video Timing
as needed (see Settings Reference below).

### STEP 4: Process

- Click "Process All [type] Files in Queue"
- Wait for the progress bar to complete
- A message will confirm when done and show the output folder path

### STEP 5: Find Your Videos

Open the `xml-to-text-video-output` folder (in the same folder as `keystroking_to_video.py`).
For batch runs, open the timestamped subfolder inside it.

> **TIP:** Use "Save Settings" to save your preferences. They load automatically
> on next startup. Use "Preview Video" (when a single file is set) to
> preview without saving.

---

## COMPLETE SETTINGS REFERENCE

### TEXT SETTINGS

**Font**
What it does: Chooses the font family (e.g. Arial, Times New Roman) used
to render all text in the video.
How to use: Select from the dropdown. Fonts are loaded from your system.

**Font Size**
What it does: Sets the text size in pixels (default 30).
How to use: Enter a number. Larger means bigger text.

**Bold**
What it does: Renders text in bold when checked.
How to use: Check or uncheck.

**Margin**
What it does: Adds padding (in pixels) around the text from the edges of
the frame (default 20).
How to use: Enter a number. Higher means more space around text.

**Show Caret**
What it does: When checked, displays a blinking cursor at the end of the
typed text. When unchecked, no cursor is shown.
How to use: Check to show cursor, uncheck to hide it.

**Hide backspace edits** (video shows only surviving text; no deleted keystrokes)
What it does: Depends on file type:

- **IDFX:** For each contiguous run of backspace keys, the program removes
  those backspaces and also removes the same number of text-producing
  keystrokes (letters, space, enter) that occur immediately *before* that
  run in time. It repeats until no backspaces remain. Example: S, A, two
  backspaces, then F-A-C-E becomes F-A-C-E (the two backspaces and the two
  keys S and A before them are dropped from the animation). Video length
  uses only time between surviving keystrokes (no long pause where deleted
  typing used to be).

- **Data TXT:** Uses a stack model (each backspace removes the preceding
  text-producing key from the timeline). Video length uses only gaps
  between surviving keystrokes.

- **XML/Word (non-uniform):** Stack model like Data TXT (SPACE/BACK/single char),
  with the same stitched timing as Data TXT when this option is on.

Does not apply to Uniform Typing Mode (Word file), which has no raw
backspace stream.
How to use: Check to enable.

**Add random fake backspaces** (video length increases; final text unchanged)
What it does: After the normal typing timeline is built, inserts random
bursts of synthetic backspace keystrokes (with matching durations), each
burst followed by re-typing the same characters that were deleted so the
final text is identical to the recording. This lengthens the video (more
frames) without changing the ending text. The expected number of bursts
scales with video length and "Approx. bursts / minute" (Poisson-distributed
mean $\approx$ rate × duration in minutes). Each burst deletes 1–5 characters from
the end of the text at a random frame (after at least one character exists),
then restores them. Each fake backspace uses a random duration in a range
derived from Word Typing Speed (so bursts feel less uniform). After the last
fake backspace, a random pause holds the same text (no new keys), then each
retyped character also uses a random duration in a similar range.
How to use: Check to enable; set bursts per minute (e.g. 2.0). Applies to
XML/Word, Data TXT, IDFX, and Uniform Typing Mode.

---

### MOVING WINDOW

**Enable Moving Window**
What it does: Restricts the visible area to a small window of text that
follows the caret. Text outside the window is replaced by mask characters.
How to use: Check to enable. When enabled, the options below become active.

**Window Size (chars)**
What it does: Controls how many characters are visible at once. The actual
visible window is about twice this value (e.g. 10 gives about 20 chars).
How to use: Enter a number (default 10).

**Window Only Current Word**
What it does: (When Moving Window is on) Further restricts the view to
only the current word being typed.
How to use: Check to enable.

**Mask (narrow)**
What it does: Character used to hide narrow/standard-width characters
(e.g. i, l, 1) when they fall outside the visible window.
How to use: Enter a single character (default: `_`).

**Mask (wide)**
What it does: Character used to hide wider characters (e.g. m, M, W, @)
when they fall outside the visible window. Keeps spacing consistent.
How to use: Enter a single character (default: `#`).

---

### UNIFORM TYPING MODE

**Uniform Typing Speed (use Word file as reference)**
What it does: Ignores original keystroke timestamps and types at a fixed
speed based on the full text from a Word (`.docx`) file. Useful when you
want consistent speed regardless of actual typing rhythm.
How to use: Check to enable. Requires a Word file (for XML/Word mode).
When enabled, the options below become active.

**Characters per Second**
What it does: Typing speed when Uniform Typing is on (default 10).
How to use: Enter a decimal (e.g. 8.5 = 8.5 characters per second).

**Video Speed Multiplier**
What it does: Scales the entire video playback speed. 1.0 = normal.
2.0 = twice as fast, 0.5 = half speed.
How to use: Enter a positive number (default 1.0).

**Word Typing Speed (s/word)**
What it does: Duration (in seconds) for the first character of each new
word. Creates a slight pause at the start of each word (default 0.15).
How to use: Enter a decimal.

**Space Duration (s)**
What it does: How long (in seconds) each space character is displayed
(default 0.25).
How to use: Enter a decimal.

---

### VIDEO TIMING CONTROLS

**Enable Video Timing Controls**
What it does: Lets you trim the output to a time range on the typing
timeline, or keep a percentage of the remaining length.
How to use: Check to enable. Options below become active.

**How timing works (both modes)**
Inputs use milliseconds on the full session timeline. The program builds
that timeline by adding up each frame duration (seconds per keystroke
frame), then cuts which logical frames to render and adjusts any frame at
the start or end that is only partly inside the range.
Export uses a fixed output framerate (20 fps). Start and end are snapped
to the nearest step of one output frame (0.05 s) so cuts line up with how
the video file is built.

**Start Time (ms) / End Time (ms)**
What it does: (Absolute mode) Where the output clip starts and stops on
the timeline, in milliseconds from the beginning.
How to use: Enter milliseconds (e.g. 1000 = 1 second). End Time 0 means
run to the end of the recording.

**Duration (%)**
What it does: (Percentage mode) Percentage of the remaining time after
the start offset. Not a frame count: it is the share of remaining duration
on the timeline (e.g. 50 = half of the time left after Start Time).
How to use: Enter 1 to 100.

**Absolute Time vs Percentage**
Absolute: Set Start Time and End Time in ms. End 0 = to end of clip.
Percentage: Set Start Time in ms (where to begin), then Duration % of the
time that remains after that point.

**Trim start/end on word boundaries (with timing trim)**
What it does: After the usual timing window is computed (absolute or
percentage), adjusts the clip so it starts at the first frame that begins
a new word on or after the nominal start, and ends at the last frame that
completes a word on or before the nominal end (typically after a space or
at the end of the text).
How to use: Check when Enable Video Timing Controls is on. Only affects
output when timing trim is active.

**Trim start/end on sentence boundaries (with timing trim)**
What it does: After the nominal timing window is computed (absolute or
percentage), moves the start to the beginning of the sentence closest to
the nominal start: the first character of the file, or the first character
typed after a sentence-ending . ! ? (ignoring trailing spaces), or the
first character on a new line after Enter. Moves the end to the closest
sentence end at or before the nominal end: the frame where the text ends
with . ! ? (after trimming spaces). If the session has no . ! ? anywhere,
it uses the frame just before a line break (Enter). If there are no line
breaks, it uses the same "end of word" rule as word boundary trim. If
both this option and word boundary trim are on, sentence boundaries take
precedence.
How to use: Check when Enable Video Timing Controls is on. Only affects
output when timing trim is active (same as word boundary trim).

---

### OPTIONS

**Save Video**
What it does: When checked, outputs MP4 files. Uncheck if you only want
to preview (useful with Preview Video button).
How to use: Check to save, uncheck to skip saving.

**Preview Video**
What it does: Generates a temporary video and opens it in your default
video player. Requires a single XML file to be set (XML/Word mode).
How to use: Click to preview.

**Save Settings**
What it does: Writes all current settings to `xml-to-text-settings.json`
in the program folder. Settings load automatically on next launch.
How to use: Click to save.

**Load Settings from CSV**
What it does: Loads settings from a CSV file (created alongside each
video output). Use to quickly reuse settings from a previous generation
or keep a written record of how videos were made.
How to use: Click and select a `_settings.csv` file.

---

## BETTER WAYS TO USE THE PROGRAM

**FONTS:**
Prefer MONOSPACE (fixed-width) fonts (e.g. Consolas, Courier New, Monaco)
when using the Moving Window feature. With monospace fonts, all characters
have the same width, so masking and spacing stay consistent and readable.
For proportional fonts, the program uses separate narrow/wide mask
characters to preserve layout; monospace still tends to look cleaner.

**MASKING CHARACTERS:**
Choose mask characters that are easy to read and not distracting.
Default `_` (underscore) and `#` (hash) work well for most uses.
Avoid using the same character as actual text (e.g. don't use "a" if
your text contains many a's).
Single characters only. Avoid symbols that might look like punctuation
in your text.

**TYPING SPEED:**
For natural-looking playback, keep Video Speed Multiplier between 0.5
and 2.0 unless you need extreme slow/fast motion.
Uniform Typing Mode is best when you have a clean Word file and want
consistent speed; leave it off if you want to preserve original typing
rhythm from the input data.

**BATCH PROCESSING:**
Add all files to the queue before processing to avoid starting multiple
batch runs.
Batch runs with 2+ files create a timestamped subfolder so each run
stays organized.

**FILE FORMATS:**
Ensure XML files follow the expected structure (keyboard events with
`output` and `startTime`).
Large `data.txt` files are handled efficiently via `ijson`, which is included in `requirements.txt`.

---

## USEFUL POINTERS

- The output folder `xml-to-text-video-output` is created automatically
  when you run your first process. It appears in the same folder as `keystroking_to_video.py`.

- Settings are stored in `xml-to-text-settings.json` in the program folder.
  You can edit this file manually if needed (use valid JSON).

- Each video output has a matching `_settings.csv` file with all settings used.
  Use "Load Settings from CSV" to reload those settings and save time, or
  keep the CSV files as written records of how each video was generated.

- Preview Video works only for XML/Word mode with a single XML file
  selected. Batch mode does not support preview.

- If a font cannot be loaded, the program falls back to a system default
  and may show a warning.

- Processing runs in the background. You can keep using the window, but
  avoid starting another process until the current one finishes.

---

## BACKGROUND: HOW THE PROGRAM FUNCTIONS AND PLUGINS

### HOW THE PROGRAM WORKS

The application reads keystroke data from input files and turns it into
a video animation:

1. **PARSING:** Reads the input file (XML, JSON `data.txt`, or IDFX) and extracts
   keyboard events: which key was pressed and when (timestamps).

2. **TEXT RECONSTRUCTION:** Builds a sequence of text states (snapshots of
   the text after each keystroke). Handles space, backspace, and special
   keys. Optionally uses a Word file for uniform typing (ignores timestamps
   and types at fixed speed).

3. **FRAME GENERATION:** For each text state, creates an image (1280x720) with
   text wrapped to fit the frame, optional moving window (masking text outside
   a visible region), blinking caret (optional), and font/size/margin applied.

4. **VIDEO ASSEMBLY:** Converts the image sequence into an MP4 using frame
   durations from the timestamps. Applies speed multiplier and timing
   controls (trimming).

Threading is used so long operations (parsing, frame generation, video
encoding) run in the background without freezing the interface.

### HOW THE PLUGINS/LIBRARIES ARE USED

- **`tkinter`:** Built-in GUI (windows, buttons, dropdowns, file dialogs).
- **`lxml` (etree):** Parses XML and IDFX files to extract keyboard events.
- **`python-docx` (Document):** Reads Word (`.docx`) files for uniform typing mode.
- **`Pillow` (PIL):** Creates images, draws text, loads fonts. Used for every frame of the video.
- **`moviepy` (ImageSequenceClip):** Assembles image frames into an MP4 video.
- **`ijson`:** Streams through large JSON `data.txt` files without loading everything into memory.
- **`numpy` (via moviepy):** Converts images to arrays for video encoding.

