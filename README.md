# MazeEditor

A reusable web app for viewing and editing crossword-style maze puzzles with Telugu words.

## Features

- Live maze grid rendering using `mazesData.csv` for ground-truth word positions
- Inline word editing with **English → Telugu transliteration** (Google-style: `amma` → అమ్మ, `bhuusaaraM` → భూసారం)
- **Marked / selected word tracking** persisted to disk (`.marked_words.json`) — see which words you've curated per maze
- Single-word **alternate suggestions** that respect every crossing constraint at the slot
- **Pair alternates (v8)** — pick two crossing words and see compatible `(w1', w2')` candidate pairs that agree at the intersection cell while each fits all other crossing constraints
- Reverse / direction-aware grid placement preview

## Pair Alternates (v8)

1. In the word list, click the 🔗 badge next to any word — it turns orange.
2. Crossing words become highlighted with an orange ring.
3. Click 🔗 on one of the highlighted words.
4. The suggestion panel lists every compatible `(w1', w2')` pair, marked with the agreed intersection syllable. `⟲` marks reverse-only fits.
5. Click a row to atomically replace both words.

Click the same first 🔗 again, or the **Cancel** button, to abort pair mode.

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
- `common_telugu_cleaned_ready_to_use.csv` — preferred pool with explicit syllable breakdown

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
