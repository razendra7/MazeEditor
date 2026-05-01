# MazeEditor

A reusable web app for viewing and editing crossword-style maze puzzles with Telugu words.

## Features

- Live maze grid rendering using `mazesData.csv` for ground-truth word positions
- Inline word editing with **English → Telugu transliteration** (Google-style: `amma` → అమ్మ, `bhuusaaraM` → భూసారం)
- **Marked / selected word tracking** persisted to disk (`edited_words.json`) — see which words you've curated per maze
- Word-suggestion lookup against an `AvailableWords.csv` pool
- Reverse / direction-aware grid placement preview

## Usage

```bash
python server.py <puzzle_folder>
```

The `<puzzle_folder>` must contain:

| File | Columns |
|------|---------|
| `SelectedWords.csv` | mazeid, wordnumber, fullword |
| `mazesData.csv` | mazeid, wordid, wordnumber, startrow, startcolumn, length, direction, isreverse, fullword, string_agg |
| `common_telugu_cleaned.csv` | telugu_word, english_meaning, ... |
| `*.png` | maze images named `{number}_{id}.png` |

Optional:
- `AvailableWords.csv` (column: `telugu_word`) — words not yet used, for suggestions

Then open http://127.0.0.1:5000 in a browser.

## Telugu Input

The transliterator runs in the browser. Keys to know:

| Type | Get |
|------|-----|
| `amma` | అమ్మ |
| `naanna` | నాన్న |
| `telugu` | తెలుగు |
| `bhuusaaraM` | భూసారం |
| capital `M` | ం (anusvara) |
| capital `H` | ః (visarga) |

Press **Enter** to save, **Esc** to cancel an edit.

## Data privacy

This repository contains **only the editor code**. No puzzle data, images, or word lists are committed (see `.gitignore`). Point the server at any compatible puzzle folder.
