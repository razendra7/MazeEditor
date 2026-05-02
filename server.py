"""
Telugu Maze Word Editor v8 — Pair Alternates
A reusable web app for viewing and editing maze puzzles with Telugu words.

v8 adds: alternate-word suggestions for a PAIR of crossing/linked words
considered together (so any candidate pair (w1', w2') agrees at the
intersection cell and each fits all OTHER crossing constraints).

Usage:
    python server.py <puzzle_folder>
    python server.py c:\\myprograms\\Book1

The puzzle folder must contain:
    - SelectedWords.csv   (columns: mazeid, wordnumber, fullword)
    - mazesData.csv       (columns: mazeid, wordid, wordnumber, startrow, startcolumn,
                           length, direction, isreverse, fullword, string_agg)
    - common_telugu_cleaned.csv (columns: telugu_word, english_meaning, ...)
    - *.png maze images    (named as {number}_{id}.png where {number} is the maze ID)

Optional:
    - AvailableWords.csv  (column: telugu_word) — words not yet used, for suggestions
"""

import csv
import os
import re
import sys
import json
import shutil
import unicodedata
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, send_file, request

app = Flask(__name__)

# Will be set from command line argument
PUZZLE_DIR = None
DATA = None


class PuzzleData:
    """Holds all puzzle data and supports reloading/saving."""

    def __init__(self, puzzle_dir):
        self.puzzle_dir = puzzle_dir
        self.words_csv = os.path.join(puzzle_dir, "SelectedWords.csv")
        self.mazes_data_csv = os.path.join(puzzle_dir, "mazesData.csv")
        self.dict_csv = os.path.join(puzzle_dir, "common_telugu_cleaned.csv")
        self.available_csv = os.path.join(puzzle_dir, "AvailableWords.csv")
        self.edited_words_file = os.path.join(puzzle_dir, ".edited_words.json")
        self.marked_words_file = os.path.join(puzzle_dir, ".marked_words.json")
        self.blacklist_file = os.path.join(puzzle_dir, ".blacklist.json")
        self.reload()

    def reload(self):
        self.words_by_maze = {}
        self.word_info = {}
        self.image_map = {}
        self.word_to_mazes = {}
        self.available_words = []
        self.available_set = set()
        self.available_by_length = {}
        self.edited_words = set()
        self.marked_words = set()  # (maze_id, wordnumber, word) tuples
        self.blacklist = set()
        self.maze_positions = {}  # maze_id -> list of position dicts from mazesData.csv
        self._load_blacklist()
        self._load_selected_words()
        self._load_mazes_data()
        self._load_dictionary()
        self._load_images()
        self._build_word_index()
        self._load_available_words()
        self._load_edited_words()
        self._load_marked_words()
        self.maze_ids = sorted(self.words_by_maze.keys(), key=int)

    def _load_edited_words(self):
        """Load persisted edited words tracking."""
        if os.path.exists(self.edited_words_file):
            try:
                with open(self.edited_words_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.edited_words = set(tuple(x) for x in data)
            except Exception:
                self.edited_words = set()

    def _save_edited_words(self):
        """Persist edited words tracking to disk."""
        with open(self.edited_words_file, "w", encoding="utf-8") as f:
            json.dump(list(self.edited_words), f)

    def _load_marked_words(self):
        """Load persisted marked/flagged words."""
        if os.path.exists(self.marked_words_file):
            try:
                with open(self.marked_words_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.marked_words = set(tuple(x) for x in data)
            except Exception:
                self.marked_words = set()

    def _save_marked_words(self):
        """Persist marked words to disk."""
        with open(self.marked_words_file, "w", encoding="utf-8") as f:
            json.dump(list(self.marked_words), f, ensure_ascii=False)

    def _load_blacklist(self):
        """Load persisted blacklist of invalid words."""
        if os.path.exists(self.blacklist_file):
            try:
                with open(self.blacklist_file, "r", encoding="utf-8") as f:
                    self.blacklist = set(json.load(f))
            except Exception:
                self.blacklist = set()

    def _save_blacklist(self):
        """Persist blacklist to disk."""
        with open(self.blacklist_file, "w", encoding="utf-8") as f:
            json.dump(sorted(self.blacklist), f, ensure_ascii=False, indent=2)

    def mark_invalid(self, word):
        """Mark a word as invalid:
        - Added to the persistent blacklist
        - Removed from the available pool (filters all alternates / suggestions)
        - Removed from any maze that currently uses it (those slots become empty)
        Returns dict with affected mazes.
        """
        if not word:
            return {"word": word, "added": False, "removed_from": []}

        already = word in self.blacklist
        self.blacklist.add(word)
        self._save_blacklist()
        self._remove_from_available(word)

        # Remove the word from every maze that currently has it.
        affected = sorted(list(self.word_to_mazes.get(word, set())), key=int)
        for mid in affected:
            for pos in self.maze_positions.get(mid, []):
                if pos.get("fullword") == word:
                    self.delete_word(mid, pos["wordnumber"], pos["direction"])

        return {
            "word": word,
            "added": not already,
            "removed_from": affected,
        }

    def unmark_invalid(self, word):
        """Remove a word from the blacklist and put it back in the available pool."""
        if word not in self.blacklist:
            return {"word": word, "removed": False}
        self.blacklist.discard(word)
        self._save_blacklist()
        # Restore to available pool only if not currently used in any maze
        if word and (word not in self.word_to_mazes or not self.word_to_mazes[word]):
            self._add_to_available(word)
        return {"word": word, "removed": True}

    def toggle_mark_word(self, maze_id, wordnumber, word):
        """Toggle the marked state of a word. Returns new marked state."""
        key = (maze_id, wordnumber, word)
        if key in self.marked_words:
            self.marked_words.discard(key)
            self._save_marked_words()
            return False
        else:
            self.marked_words.add(key)
            self._save_marked_words()
            return True

    def get_marked_words_list(self):
        """Get all marked words with their maze info, grouped for display."""
        result = []
        for maze_id, wordnumber, word in sorted(self.marked_words, key=lambda x: (int(x[0]), int(x[1]))):
            info = self.word_info.get(word, {})
            # Find direction from maze_positions
            direction = ''
            positions = self.maze_positions.get(maze_id, [])
            for p in positions:
                if p['wordnumber'] == wordnumber and p['fullword'] == word:
                    direction = p['direction']
                    break
            if not direction:
                # Word may have been edited; find by wordnumber
                pos_list = [p for p in positions if p['wordnumber'] == wordnumber]
                if pos_list:
                    direction = pos_list[0]['direction']
            result.append({
                'maze_id': maze_id,
                'wordnumber': wordnumber,
                'word': word,
                'direction': direction,
                'meaning': info.get('meaning', ''),
                'hint': info.get('hint_tel', '') or info.get('hint_eng', ''),
            })
        return result

    def _load_selected_words(self):
        self.words_by_maze = {}
        with open(self.words_csv, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                mid = row["mazeid"].strip()
                word = row["fullword"].strip()
                wnum = row["wordnumber"].strip()
                self.words_by_maze.setdefault(mid, []).append(
                    {"word": word, "wordnumber": wnum}
                )
        # Deduplicate per maze, preserving order
        # Key by (word, wordnumber) so multiple empty slots (one per wordnumber)
        # are preserved.
        for mid in self.words_by_maze:
            seen = set()
            unique = []
            for entry in self.words_by_maze[mid]:
                key = (entry["word"], entry["wordnumber"])
                if key not in seen:
                    seen.add(key)
                    unique.append(entry)
            self.words_by_maze[mid] = unique

    def _load_mazes_data(self):
        """Load ground-truth word positions from mazesData.csv.
        Contains direction, reverse flag, start position, length, syllables."""
        self.maze_positions = {}
        if not os.path.exists(self.mazes_data_csv):
            print("WARNING: mazesData.csv not found — grid rendering will be limited")
            return
        with open(self.mazes_data_csv, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                mid = row["mazeid"].strip()
                self.maze_positions.setdefault(mid, []).append({
                    "wordnumber": row["wordnumber"].strip(),
                    "start_row": int(row["startrow"].strip()),
                    "start_col": int(row["startcolumn"].strip()),
                    "length": int(row["length"].strip()),
                    "direction": row["direction"].strip(),
                    "is_reverse": row["isreverse"].strip() == "1",
                    "fullword": row["fullword"].strip(),
                    "syllables_str": row.get("string_agg", "").strip(),
                })
        print(f"Loaded mazesData.csv: {sum(len(v) for v in self.maze_positions.values())} entries across {len(self.maze_positions)} mazes")

    def _load_dictionary(self):
        self.word_info = {}
        if not os.path.exists(self.dict_csv):
            return
        with open(self.dict_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            word_col = next((c for c in reader.fieldnames if "telugu_word" in c), None)
            if not word_col:
                return
            for row in reader:
                w = row[word_col].strip()
                self.word_info[w] = {
                    "meaning": row.get("english_meaning", "").strip(),
                    "hint_eng": row.get("crossword_hint_english", "").strip(),
                    "hint_tel": row.get("crossword_hint_telugu", "").strip(),
                }

    def _load_images(self):
        self.image_map = {}
        for fname in os.listdir(self.puzzle_dir):
            if fname.lower().endswith(".png"):
                match = re.match(r"^(\d+)_", fname)
                if match:
                    self.image_map[match.group(1)] = fname

    def _build_word_index(self):
        self.word_to_mazes = {}
        for mid, entries in self.words_by_maze.items():
            for entry in entries:
                self.word_to_mazes.setdefault(entry["word"], set()).add(mid)

    def _load_available_words(self):
        """Load available words and index by Telugu syllable count and first character."""
        self.available_words = []
        self.available_set = set()
        self.available_by_length = {}  # {syllable_count: {first_base_char: [words]}}

        # Try to load from the ready_to_use CSV (has letter breakdown)
        ready_csv = os.path.join(self.puzzle_dir, "common_telugu_cleaned_ready_to_use .csv")
        if not os.path.exists(ready_csv):
            ready_csv = os.path.join(self.puzzle_dir, "common_telugu_cleaned_ready_to_use.csv")

        # Build set of selected words for filtering
        selected = set()
        for entries in self.words_by_maze.values():
            for entry in entries:
                selected.add(entry["word"])

        def _add_word(word, syllable_count=None):
            """Add a word to the available pool if not already present."""
            if word in self.available_set:
                return
            if word in self.blacklist:
                return
            self.available_set.add(word)
            self.available_words.append(word)
            slen = syllable_count if syllable_count else self._telugu_syllable_count(word)
            first = self._first_base_char(word)
            self.available_by_length.setdefault(slen, {}).setdefault(first, []).append(word)

        if os.path.exists(ready_csv):
            with open(ready_csv, "r", encoding="utf-8") as f:
                next(f)  # skip header
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        word = parts[0]
                        syllable_count = len(parts) - 1
                        if word not in selected:
                            _add_word(word, syllable_count)
        elif os.path.exists(self.available_csv):
            with open(self.available_csv, "r", encoding="utf-8-sig") as f:
                next(f)
                for line in f:
                    word = line.strip()
                    if word and word not in selected:
                        _add_word(word)

        # Also load words from wmpwordsimple2.csv if it exists
        wmp_csv = os.path.join(self.puzzle_dir, "wmpwordsimple2.csv")
        if os.path.exists(wmp_csv):
            added = 0
            with open(wmp_csv, "r", encoding="utf-8-sig") as f:
                import csv as csv_mod
                reader = csv_mod.reader(f)
                for row in reader:
                    for cell in row:
                        for w in cell.split(","):
                            w = w.strip()
                            if w and w not in selected and w not in self.available_set:
                                _add_word(w)
                                added += 1
            if added:
                print(f"Added {added} words from wmpwordsimple2.csv")

    @staticmethod
    def _first_base_char(word):
        """Get the first base consonant/vowel (skip combining marks)."""
        import unicodedata
        for c in word:
            if unicodedata.category(c)[0] != 'M':
                return c
        return word[0] if word else ''

    @staticmethod
    def _telugu_syllable_count(word):
        """Count Telugu syllables (base characters, not combining marks)."""
        import unicodedata
        return sum(1 for c in word if unicodedata.category(c)[0] != 'M')

    def suggest_words(self, current_word, prefix=""):
        """Get suggestions matching the syllable count and first character."""
        wlen = self._telugu_syllable_count(current_word)
        if wlen == 0:
            return []

        first_char = self._first_base_char(prefix) if prefix else self._first_base_char(current_word)
        candidates = self.available_by_length.get(wlen, {}).get(first_char, [])

        if prefix and len(prefix) > 1:
            candidates = [w for w in candidates if w.startswith(prefix)]

        return candidates[:50]  # limit results

    def get_alternates(self, word):
        """Get ALL available words with same syllable count (any first letter)."""
        wlen = self._telugu_syllable_count(word)
        if wlen == 0:
            return []
        bucket = self.available_by_length.get(wlen, {})
        result = []
        for words_list in bucket.values():
            result.extend(words_list)
        result.sort()
        return result

    def get_maze_alternates(self, maze_id, word_num_str, direction):
        """Get alternate words that actually fit in the maze at a specific position.
        A word fits if at every intersection with a crossing word, the syllable matches.
        Words can be placed forward or reversed."""
        grid_data = self.get_maze_grid(maze_id)
        if not grid_data:
            return [], []

        # Find the target word position
        target_pos = None
        for wp in grid_data['word_positions']:
            if str(wp['word_num']) == word_num_str and wp['direction'] == direction:
                target_pos = wp
                break
        if not target_pos:
            return [], []

        target_word = target_pos['word']
        target_len = target_pos['length']

        # Build a map of all cells -> syllable value from crossing words
        cell_constraints = {}  # cell (r,c) -> syllable value from crossing word
        for wp in grid_data['word_positions']:
            if wp['word_num'] == target_pos['word_num'] and wp['direction'] == target_pos['direction']:
                continue  # skip the target word itself
            syls = self._split_telugu_syllables(wp['word'])
            if wp['reversed']:
                syls = list(reversed(syls))
            for i in range(wp['length']):
                r = wp['start_row'] + (i if wp['direction'] == 'Down' else 0)
                c = wp['start_col'] + (0 if wp['direction'] == 'Down' else i)
                if i < len(syls):
                    cell_constraints[(r, c)] = syls[i]

        # Get the target word's cell positions (in grid order)
        target_cells = []
        for i in range(target_len):
            r = target_pos['start_row'] + (i if direction == 'Down' else 0)
            c = target_pos['start_col'] + (0 if direction == 'Down' else i)
            target_cells.append((r, c))

        # Build cell-based constraints: cell_index -> required_syllable
        cell_idx_constraints = {}
        for cell_idx, (r, c) in enumerate(target_cells):
            if (r, c) in cell_constraints:
                cell_idx_constraints[cell_idx] = cell_constraints[(r, c)]

        # Get all candidate words with correct syllable count
        bucket = self.available_by_length.get(target_len, {})
        candidates = []
        for words_list in bucket.values():
            candidates.extend(words_list)

        # Filter: check each candidate both forward and reversed
        fitting = []
        seen = set()
        for cand in candidates:
            if cand == target_word or cand in seen:
                continue
            syls = self._split_telugu_syllables(cand)
            if len(syls) != target_len:
                continue

            # Check forward placement
            fwd_fits = True
            for cell_idx, required in cell_idx_constraints.items():
                if cell_idx < len(syls) and syls[cell_idx] != required:
                    fwd_fits = False
                    break

            # Check reversed placement
            rev_syls = list(reversed(syls))
            rev_fits = True
            for cell_idx, required in cell_idx_constraints.items():
                if cell_idx < len(rev_syls) and rev_syls[cell_idx] != required:
                    rev_fits = False
                    break

            if fwd_fits or rev_fits:
                label = cand
                if rev_fits and not fwd_fits:
                    label = cand + " ⟲"  # mark as reverse-only fit
                elif rev_fits and fwd_fits:
                    label = cand + " ↔"  # fits both ways
                fitting.append({"word": cand, "label": label, "fwd": fwd_fits, "rev": rev_fits})
                seen.add(cand)

        fitting.sort(key=lambda x: x['word'])
        # Constraint info for display
        constraint_info = [
            {'pos': idx, 'letter': val} for idx, val in sorted(cell_idx_constraints.items())
        ]
        return fitting, constraint_info

    def get_maze_pair_alternates(self, maze_id, w1_num, w1_dir, w2_num, w2_dir, max_pairs=400):
        """Find alternate (w1', w2') word pairs for two crossing words in a maze.

        Both candidate words must:
          - have the correct syllable count for their slot,
          - satisfy every crossing constraint EXCEPT each other,
          - agree on the syllable at their shared intersection cell.

        Returns dict with:
          intersection: {row, col, w1_pos, w2_pos}
          w1: {word, direction, length, constraints: [{pos, letter}]}
          w2: {word, direction, length, constraints: [{pos, letter}]}
          pairs: [{w1, w2, syllable, w1_orient, w2_orient}], capped at max_pairs.
        """
        grid = self.get_maze_grid(maze_id)
        if not grid:
            return {"error": "no grid"}

        def find_pos(num_str, direction):
            for wp in grid['word_positions']:
                if str(wp['word_num']) == str(num_str) and wp['direction'] == direction:
                    return wp
            return None

        p1 = find_pos(w1_num, w1_dir)
        p2 = find_pos(w2_num, w2_dir)
        if not p1 or not p2:
            return {"error": "word not found"}
        if p1['direction'] == p2['direction']:
            return {"error": "words must be perpendicular to intersect"}

        # Compute cells for each word
        def cells_of(p):
            out = []
            for i in range(p['length']):
                r = p['start_row'] + (i if p['direction'] == 'Down' else 0)
                c = p['start_col'] + (0 if p['direction'] == 'Down' else i)
                out.append((r, c))
            return out

        c1 = cells_of(p1)
        c2 = cells_of(p2)
        s1 = set(c1); s2 = set(c2)
        inter = list(s1 & s2)
        if len(inter) != 1:
            return {"error": f"words don't share exactly one cell (shared={len(inter)})"}
        ir, ic = inter[0]
        i1 = c1.index((ir, ic))
        i2 = c2.index((ir, ic))

        # Build per-word non-self crossing constraints (excluding the OTHER selected word too)
        def constraints_excluding(target_pos, exclude_pos):
            target_cells = cells_of(target_pos)
            cell_set = set(target_cells)
            cross = {}  # cell -> required syllable from a 3rd word
            for wp in grid['word_positions']:
                if wp['word_num'] == target_pos['word_num'] and wp['direction'] == target_pos['direction']:
                    continue
                if wp['word_num'] == exclude_pos['word_num'] and wp['direction'] == exclude_pos['direction']:
                    continue
                wp_cells = cells_of(wp)
                syls = self._split_telugu_syllables(wp['word'])
                if wp['reversed']:
                    syls = list(reversed(syls))
                for i, cell in enumerate(wp_cells):
                    if cell in cell_set and i < len(syls):
                        cross[cell] = syls[i]
            # Map to cell-index constraints on target
            idx_constraints = {}
            for ci, cell in enumerate(target_cells):
                if cell in cross:
                    idx_constraints[ci] = cross[cell]
            return idx_constraints

        cons1 = constraints_excluding(p1, p2)
        cons2 = constraints_excluding(p2, p1)

        def fit_candidates(target_pos, idx_cons, intersection_idx):
            """Yield (candidate_word, orientation, syllable_at_intersection)."""
            tlen = target_pos['length']
            bucket = self.available_by_length.get(tlen, {})
            results = []
            for words_list in bucket.values():
                for cand in words_list:
                    syls = self._split_telugu_syllables(cand)
                    if len(syls) != tlen:
                        continue
                    # forward
                    fwd_ok = all(idx >= len(syls) or syls[idx] == val for idx, val in idx_cons.items())
                    if fwd_ok:
                        results.append((cand, 'fwd', syls[intersection_idx]))
                    rev = list(reversed(syls))
                    rev_ok = all(idx >= len(rev) or rev[idx] == val for idx, val in idx_cons.items())
                    if rev_ok:
                        results.append((cand, 'rev', rev[intersection_idx]))
            return results

        cands1 = fit_candidates(p1, cons1, i1)
        cands2 = fit_candidates(p2, cons2, i2)

        # Group word2 candidates by intersection syllable for quick join
        by_syl_2 = {}
        for cand, orient, syl in cands2:
            by_syl_2.setdefault(syl, []).append((cand, orient))

        pairs = []
        seen_pair = set()
        for cand1, orient1, syl in cands1:
            matches = by_syl_2.get(syl, [])
            for cand2, orient2 in matches:
                if cand1 == cand2:
                    continue  # don't propose the same word in both slots
                key = (cand1, orient1, cand2, orient2)
                if key in seen_pair:
                    continue
                seen_pair.add(key)
                pairs.append({
                    'w1': cand1, 'w1_orient': orient1,
                    'w2': cand2, 'w2_orient': orient2,
                    'syllable': syl,
                })
                if len(pairs) >= max_pairs:
                    break
            if len(pairs) >= max_pairs:
                break

        # Sort: prefer both-forward, then by w1 then w2
        pairs.sort(key=lambda x: (x['w1_orient'] != 'fwd', x['w2_orient'] != 'fwd', x['w1'], x['w2']))

        return {
            'intersection': {'row': ir, 'col': ic, 'w1_pos': i1, 'w2_pos': i2},
            'w1': {
                'word': p1['word'], 'word_num': p1['word_num'], 'direction': p1['direction'],
                'length': p1['length'],
                'constraints': [{'pos': k, 'letter': v} for k, v in sorted(cons1.items())],
                'fit_count': len(cands1),
            },
            'w2': {
                'word': p2['word'], 'word_num': p2['word_num'], 'direction': p2['direction'],
                'length': p2['length'],
                'constraints': [{'pos': k, 'letter': v} for k, v in sorted(cons2.items())],
                'fit_count': len(cands2),
            },
            'pairs': pairs,
            'pair_count': len(pairs),
            'truncated': len(pairs) >= max_pairs,
        }

    def find_crossing_words(self, maze_id, word_num, direction):
        """Return a list of words in the same maze that cross the given word.
        Each item: {word_num, direction, word, intersection: {row, col, my_pos, their_pos}}"""
        grid = self.get_maze_grid(maze_id)
        if not grid:
            return []
        target = None
        for wp in grid['word_positions']:
            if str(wp['word_num']) == str(word_num) and wp['direction'] == direction:
                target = wp
                break
        if not target:
            return []

        def cells_of(p):
            out = []
            for i in range(p['length']):
                r = p['start_row'] + (i if p['direction'] == 'Down' else 0)
                c = p['start_col'] + (0 if p['direction'] == 'Down' else i)
                out.append((r, c))
            return out

        my_cells = cells_of(target)
        my_set = set(my_cells)
        out = []
        for wp in grid['word_positions']:
            if wp['word_num'] == target['word_num'] and wp['direction'] == target['direction']:
                continue
            if wp['direction'] == target['direction']:
                continue  # parallels can't cross orthogonally in this format
            their = cells_of(wp)
            shared = [c for c in their if c in my_set]
            if len(shared) == 1:
                ir, ic = shared[0]
                out.append({
                    'word_num': wp['word_num'],
                    'direction': wp['direction'],
                    'word': wp['word'],
                    'intersection': {
                        'row': ir, 'col': ic,
                        'my_pos': my_cells.index((ir, ic)),
                        'their_pos': their.index((ir, ic)),
                    },
                })
        return out

    # ==================== Maze Grid Analysis ====================

    @staticmethod
    def _split_telugu_syllables(word):
        """Split a Telugu word into syllables (visual letters).
        Each syllable is a base character + any combining marks."""
        syllables = []
        current = ''
        for ch in word:
            cat = unicodedata.category(ch)
            if cat[0] == 'M':  # combining mark — attach to current syllable
                current += ch
            else:
                if current:
                    syllables.append(current)
                current = ch
        if current:
            syllables.append(current)
        return syllables

    def get_maze_grid(self, maze_id):
        """Build a complete grid with word letters filled in for a given maze.
        Uses mazesData.csv for ground-truth positions, directions, and reverse flags."""
        positions = self.maze_positions.get(maze_id, [])
        if not positions:
            return None

        # Get current words from SelectedWords.csv (may have been edited)
        entries = self.words_by_maze.get(maze_id, [])
        # Build lookup: (wordnumber, direction) -> current word from CSV
        edited_lookup = {}
        for entry in entries:
            wnum = entry['wordnumber']
            edited_lookup.setdefault(wnum, []).append(entry['word'])

        # Determine grid size from positions
        max_r = max(p['start_row'] + (p['length'] - 1 if p['direction'] == 'Down' else 0) for p in positions)
        max_c = max(p['start_col'] + (p['length'] - 1 if p['direction'] == 'Right' else 0) for p in positions)
        size = max(max_r, max_c) + 1

        # Build grid: find all cells that are occupied by any word
        occupied = set()
        numbered_cells = set()
        for pos in positions:
            for i in range(pos['length']):
                r = pos['start_row'] + (i if pos['direction'] == 'Down' else 0)
                c = pos['start_col'] + (0 if pos['direction'] == 'Down' else i)
                occupied.add((r, c))
            numbered_cells.add((pos['start_row'], pos['start_col']))

        # Initialize grid
        grid = []
        for row in range(size):
            grid_row = []
            for col in range(size):
                is_occ = (row, col) in occupied
                grid_row.append({
                    'is_blocker': not is_occ,
                    'has_number': (row, col) in numbered_cells,
                    'value': '*' if not is_occ else '',
                    'word_num': None,
                })
            grid.append(grid_row)

        # Match each position to its current word from SelectedWords.csv.
        # mazesData.csv has the original words; SelectedWords.csv may have edits.
        # Strategy: for each wordnumber, first match CSV words that still equal
        # the original, then assign remaining CSV words to remaining positions.
        word_placements = []

        # Group positions by wordnumber, preserving order
        from collections import OrderedDict
        positions_by_wnum = OrderedDict()
        for pos in positions:
            positions_by_wnum.setdefault(pos['wordnumber'], []).append(pos)

        for wnum, pos_list in positions_by_wnum.items():
            csv_words = list(edited_lookup.get(wnum, []))  # copy

            # First pass: match CSV words that equal the original mazesData word
            matched = {}  # pos_index -> csv_word
            used_csv = set()
            for pi, pos in enumerate(pos_list):
                for ci, cw in enumerate(csv_words):
                    if ci not in used_csv and cw == pos['fullword']:
                        matched[pi] = cw
                        used_csv.add(ci)
                        break

            # Second pass: assign remaining CSV words to remaining positions
            remaining_csv = [cw for ci, cw in enumerate(csv_words) if ci not in used_csv]
            remaining_pos = [pi for pi in range(len(pos_list)) if pi not in matched]
            for pi, cw in zip(remaining_pos, remaining_csv):
                matched[pi] = cw

            # Build placements
            for pi, pos in enumerate(pos_list):
                current_word = matched.get(pi, pos['fullword'])
                is_reverse = pos['is_reverse']
                current_syls = self._split_telugu_syllables(current_word)
                placed_syls = list(reversed(current_syls)) if is_reverse else list(current_syls)

                cells = []
                for i in range(pos['length']):
                    r = pos['start_row'] + (i if pos['direction'] == 'Down' else 0)
                    c = pos['start_col'] + (0 if pos['direction'] == 'Down' else i)
                    cells.append((r, c))

                word_placements.append({
                    'word_num': int(wnum),
                    'direction': pos['direction'],
                    'word': current_word,
                    'original_word': pos['fullword'],
                    'start_row': pos['start_row'],
                    'start_col': pos['start_col'],
                    'length': pos['length'],
                    'reversed': is_reverse,
                    'placed_syls': placed_syls,
                    'cells': cells,
                })

        # Build intersection map
        cell_writers = {}  # (r,c) -> [(placement_idx, syllable_index)]
        for pi, wp in enumerate(word_placements):
            for si, (r, c) in enumerate(wp['cells']):
                cell_writers.setdefault((r, c), []).append((pi, si))

        intersections = {k: v for k, v in cell_writers.items() if len(v) >= 2}

        # Write cell values
        # First pass: non-intersection cells
        for pi, wp in enumerate(word_placements):
            for si, (r, c) in enumerate(wp['cells']):
                if r < size and c < size and si < len(wp['placed_syls']):
                    if (r, c) not in intersections:
                        grid[r][c]['value'] = wp['placed_syls'][si]
                    if grid[r][c]['word_num'] is None:
                        grid[r][c]['word_num'] = wp['word_num']

        # Second pass: resolve intersections
        for (r, c), writers in intersections.items():
            candidates = []
            for pi, si in writers:
                wp = word_placements[pi]
                if si < len(wp['placed_syls']):
                    wnum_str = str(wp['word_num'])
                    explicitly_edited = (maze_id, wnum_str) in self.edited_words
                    candidates.append({
                        'value': wp['placed_syls'][si],
                        'pi': pi,
                        'edited': explicitly_edited,
                    })

            values = set(c['value'] for c in candidates)
            if len(values) == 1:
                grid[r][c]['value'] = candidates[0]['value']
            else:
                # Conflict: edited word wins, otherwise first writer
                edited_cands = [c for c in candidates if c['edited']]
                winner = edited_cands[0] if edited_cands else candidates[0]
                grid[r][c]['value'] = winner['value']
                grid[r][c]['conflict'] = True

        # Build response
        word_positions = []
        for wp in word_placements:
            word_positions.append({
                'word_num': wp['word_num'],
                'direction': wp['direction'],
                'word': wp['word'],
                'start_row': wp['start_row'],
                'start_col': wp['start_col'],
                'length': wp['length'],
                'reversed': wp['reversed'],
            })

        flat_grid = []
        for row in range(size):
            for col in range(size):
                cell = grid[row][col]
                flat_grid.append({
                    'r': row, 'c': col,
                    'blocker': cell['is_blocker'],
                    'numbered': cell['has_number'],
                    'value': cell.get('value', ''),
                    'wn': cell.get('word_num'),
                    'conflict': cell.get('conflict', False),
                })

        return {
            'size': size,
            'cells': flat_grid,
            'word_positions': word_positions,
        }

    def get_alternate_count(self, word):
        """Count available words with same syllable count."""
        wlen = self._telugu_syllable_count(word)
        if wlen == 0:
            return 0
        bucket = self.available_by_length.get(wlen, {})
        return sum(len(v) for v in bucket.values())

    def _remove_from_available(self, word):
        """Remove a word from the available pool."""
        if word not in self.available_set:
            return
        self.available_set.discard(word)
        slen = self._telugu_syllable_count(word)
        first = self._first_base_char(word)
        bucket = self.available_by_length.get(slen, {}).get(first, [])
        if word in bucket:
            bucket.remove(word)

    def _add_to_available(self, word):
        """Add a word to the available pool."""
        if not word:
            return
        if word in self.blacklist:
            return
        if word in self.available_set:
            return
        self.available_set.add(word)
        self.available_words.append(word)
        slen = self._telugu_syllable_count(word)
        first = self._first_base_char(word)
        self.available_by_length.setdefault(slen, {}).setdefault(first, []).append(word)

    def update_word(self, maze_id, old_word, new_word, wordnumber=None, direction=None, orient=None):
        """Update a word in a maze and save to CSV.

        If wordnumber+direction+orient are provided, the slot's is_reverse flag in
        mazesData.csv is flipped when the chosen orientation does not match the
        slot's current is_reverse. This is what makes a "reverse-only" candidate
        actually display in reverse instead of being placed forward.
        """
        entries = self.words_by_maze.get(maze_id, [])
        updated = False
        updated_wordnum = None
        for entry in entries:
            if entry["word"] == old_word:
                if wordnumber is not None and str(entry["wordnumber"]) != str(wordnumber):
                    continue
                entry["word"] = new_word
                updated_wordnum = entry["wordnumber"]
                updated = True
                break

        if not updated:
            return False

        # Mirror the change into mazesData positions so the grid renderer
        # picks up the right is_reverse flag and fullword.
        positions = self.maze_positions.get(maze_id, [])
        target_pos = None
        if wordnumber is not None and direction:
            for pos in positions:
                if (str(pos["wordnumber"]) == str(wordnumber)
                        and pos["direction"] == direction
                        and pos["fullword"] == old_word):
                    target_pos = pos; break
            if target_pos is None:
                for pos in positions:
                    if str(pos["wordnumber"]) == str(wordnumber) and pos["direction"] == direction:
                        target_pos = pos; break
        else:
            # legacy single-word path: match by old fullword
            for pos in positions:
                if pos["fullword"] == old_word:
                    target_pos = pos; break

        mazes_data_dirty = False
        if target_pos is not None:
            if target_pos["fullword"] != new_word:
                target_pos["fullword"] = new_word
                mazes_data_dirty = True
            if orient in ("fwd", "rev"):
                desired_reverse = (orient == "rev")
                if target_pos["is_reverse"] != desired_reverse:
                    target_pos["is_reverse"] = desired_reverse
                    mazes_data_dirty = True

        # Track this word as edited so grid rendering gives it priority
        self.edited_words.add((maze_id, updated_wordnum))
        self._save_edited_words()
        self._save_words_csv()
        if mazes_data_dirty:
            self._save_mazes_data_csv()
        self._build_word_index()
        # Remove new_word from available pool (it's now in a maze)
        self._remove_from_available(new_word)
        # Add old_word back to available pool (no longer in any maze)
        if old_word not in self.word_to_mazes or len(self.word_to_mazes[old_word]) == 0:
            self._add_to_available(old_word)
        return True

    def delete_word(self, maze_id, wordnumber, direction):
        """Mark a word slot as empty (delete the word from this maze).

        - SelectedWords.csv: the matching entry's fullword becomes "" (slot kept)
        - mazesData.csv: the matching position's fullword becomes "" (geometry kept)
        - Removes the word from this maze in word_to_mazes; if the word is no
          longer used in any maze, it goes back into the available pool.
        Returns the deleted word, or None if no slot matched.
        """
        positions = self.maze_positions.get(maze_id, [])
        target_pos = None
        for pos in positions:
            if (str(pos["wordnumber"]) == str(wordnumber)
                    and pos["direction"] == direction):
                target_pos = pos
                break
        if target_pos is None:
            return None

        deleted_word = target_pos.get("fullword", "")
        if not deleted_word:
            # Already empty
            return None

        # Find the SelectedWords entry that matches this position by
        # (wordnumber, word == current fullword). Falls back to the first
        # entry with the same wordnumber if no exact match.
        entries = self.words_by_maze.get(maze_id, [])
        target_entry = None
        for entry in entries:
            if (str(entry["wordnumber"]) == str(wordnumber)
                    and entry["word"] == deleted_word):
                target_entry = entry
                break
        if target_entry is None:
            for entry in entries:
                if str(entry["wordnumber"]) == str(wordnumber) and entry["word"]:
                    target_entry = entry
                    break
        if target_entry is not None:
            target_entry["word"] = ""

        target_pos["fullword"] = ""

        self.edited_words.add((maze_id, str(wordnumber)))
        self._save_edited_words()
        self._save_words_csv()
        self._save_mazes_data_csv()
        self._build_word_index()

        if (deleted_word not in self.word_to_mazes
                or len(self.word_to_mazes[deleted_word]) == 0):
            self._add_to_available(deleted_word)

        return deleted_word

    def get_empty_slots(self):
        """Return a list of empty slots across all mazes (mazesData fullword == "").
        [{maze_id, wordnumber, direction, length, start_row, start_col}, ...]
        Sorted by maze_id (numeric) then wordnumber.
        """
        results = []
        for mid, positions in self.maze_positions.items():
            for pos in positions:
                if pos.get("fullword"):
                    continue
                results.append({
                    "maze_id": mid,
                    "wordnumber": int(pos["wordnumber"]),
                    "direction": pos["direction"],
                    "length": pos["length"],
                    "start_row": pos["start_row"],
                    "start_col": pos["start_col"],
                })
        results.sort(key=lambda r: (int(r["maze_id"]), r["wordnumber"], r["direction"]))
        return results

    def _save_mazes_data_csv(self):
        """Write current self.maze_positions back to mazesData.csv (with backup).
        Preserves all columns; only fullword and isreverse are mutated when
        positions are matched by (mazeid, wordnumber, direction, startrow, startcolumn).
        """
        if not os.path.exists(self.mazes_data_csv):
            return
        backup_dir = os.path.join(self.puzzle_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"mazesData_{timestamp}.csv")
        shutil.copy2(self.mazes_data_csv, backup_path)

        with open(self.mazes_data_csv, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = [r for r in reader]

        idx = {c: header.index(c) for c in header}

        for row in rows:
            mid = row[idx["mazeid"]].strip()
            wnum = row[idx["wordnumber"]].strip()
            direction = row[idx["direction"]].strip()
            try:
                startrow = int(row[idx["startrow"]].strip())
                startcol = int(row[idx["startcolumn"]].strip())
            except ValueError:
                continue
            for pos in self.maze_positions.get(mid, []):
                if (str(pos["wordnumber"]) == wnum
                        and pos["direction"] == direction
                        and pos["start_row"] == startrow
                        and pos["start_col"] == startcol):
                    row[idx["fullword"]] = pos["fullword"]
                    row[idx["isreverse"]] = "1" if pos["is_reverse"] else "0"
                    break

        with open(self.mazes_data_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(header)
            for r in rows:
                writer.writerow(r)

    def update_hint(self, word, hint_tel, hint_eng=None):
        """Update hints for a word in the dictionary and save."""
        if word not in self.word_info:
            self.word_info[word] = {"meaning": "", "hint_eng": "", "hint_tel": ""}

        self.word_info[word]["hint_tel"] = hint_tel
        if hint_eng is not None:
            self.word_info[word]["hint_eng"] = hint_eng

        self._save_dict_csv()
        return True

    def update_meaning(self, word, meaning):
        """Update meaning for a word in the dictionary and save."""
        if word not in self.word_info:
            self.word_info[word] = {"meaning": "", "hint_eng": "", "hint_tel": ""}

        self.word_info[word]["meaning"] = meaning
        self._save_dict_csv()
        return True

    def _save_dict_csv(self):
        """Save dictionary back to common_telugu_cleaned.csv with a backup."""
        if not os.path.exists(self.dict_csv):
            return

        backup_dir = os.path.join(self.puzzle_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(
            backup_dir, f"common_telugu_cleaned_{timestamp}.csv"
        )
        shutil.copy2(self.dict_csv, backup_path)

        # Read original to preserve all columns
        rows = []
        with open(self.dict_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            word_col = next((c for c in fieldnames if "telugu_word" in c), None)
            for row in reader:
                w = row[word_col].strip()
                if w in self.word_info:
                    info = self.word_info[w]
                    row["english_meaning"] = info.get("meaning", row.get("english_meaning", ""))
                    row["crossword_hint_english"] = info.get("hint_eng", row.get("crossword_hint_english", ""))
                    row["crossword_hint_telugu"] = info.get("hint_tel", row.get("crossword_hint_telugu", ""))
                rows.append(row)

        with open(self.dict_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def _save_words_csv(self):
        """Save all words back to SelectedWords.csv with a backup."""
        # Create backup
        backup_dir = os.path.join(self.puzzle_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"SelectedWords_{timestamp}.csv")
        shutil.copy2(self.words_csv, backup_path)

        # Write updated CSV
        with open(self.words_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["mazeid", "wordnumber", "fullword"])
            for mid in sorted(self.words_by_maze.keys(), key=int):
                for entry in self.words_by_maze[mid]:
                    writer.writerow([mid, entry["wordnumber"], entry["word"]])


# --- Routes ---

@app.route("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.route("/api/info")
def get_info():
    """Return puzzle folder info."""
    folder_name = os.path.basename(DATA.puzzle_dir)
    return jsonify({
        "folder": folder_name,
        "path": DATA.puzzle_dir,
        "maze_count": len(DATA.maze_ids),
        "word_count": sum(len(v) for v in DATA.words_by_maze.values()),
        "dict_size": len(DATA.word_info),
        "available_words": len(DATA.available_words),
    })


@app.route("/api/mazes")
def get_mazes():
    return jsonify(DATA.maze_ids)


@app.route("/api/maze/<maze_id>")
def get_maze(maze_id):
    entries = DATA.words_by_maze.get(maze_id, [])

    # Get direction info from mazesData using same matching as get_maze_grid
    positions = DATA.maze_positions.get(maze_id, [])
    # Group positions by wordnumber
    pos_by_wnum = {}
    for p in positions:
        pos_by_wnum.setdefault(p['wordnumber'], []).append(p)

    # Group CSV entries by wordnumber, preserving order
    entries_by_wnum = {}
    for i, entry in enumerate(entries):
        entries_by_wnum.setdefault(entry['wordnumber'], []).append((i, entry))

    # For each wordnumber, match CSV words to mazesData positions
    entry_direction = {}  # entry_index -> direction
    for wnum, wnum_entries in entries_by_wnum.items():
        wnum_positions = pos_by_wnum.get(wnum, [])
        csv_words = [e['word'] for _, e in wnum_entries]
        entry_indices = [i for i, _ in wnum_entries]

        # First pass: match CSV words that equal the original mazesData word
        matched_entry = {}  # entry_list_idx -> position
        used_pos = set()
        for ei, cw in enumerate(csv_words):
            for pi, pos in enumerate(wnum_positions):
                if pi not in used_pos and cw == pos['fullword']:
                    matched_entry[ei] = pos
                    used_pos.add(pi)
                    break

        # Second pass: assign remaining
        remaining_pos = [pos for pi, pos in enumerate(wnum_positions) if pi not in used_pos]
        remaining_entries = [ei for ei in range(len(csv_words)) if ei not in matched_entry]
        for ei, pos in zip(remaining_entries, remaining_pos):
            matched_entry[ei] = pos

        for ei in range(len(csv_words)):
            pos = matched_entry.get(ei)
            entry_direction[entry_indices[ei]] = pos['direction'] if pos else ''

    words = []
    for i, entry in enumerate(entries):
        info = DATA.word_info.get(entry["word"], {})
        direction = entry_direction.get(i, '')
        syl_count = DATA._telugu_syllable_count(entry["word"])
        # For empty slots, fall back to the slot's geometric length
        if syl_count == 0:
            for pos in DATA.maze_positions.get(maze_id, []):
                if (str(pos['wordnumber']) == str(entry['wordnumber'])
                        and pos['direction'] == direction):
                    syl_count = pos['length']
                    break
        words.append({
            "word": entry["word"],
            "wordnumber": entry["wordnumber"],
            "direction": direction,
            "meaning": info.get("meaning", ""),
            "hint": info.get("hint_tel", "") or info.get("hint_eng", ""),
            "syllables": syl_count,
            "is_empty": entry["word"] == "",
        })
    image_file = DATA.image_map.get(maze_id, "")
    return jsonify({"maze_id": maze_id, "image": image_file, "words": words})


@app.route("/api/maze_grid/<maze_id>")
def get_maze_grid(maze_id):
    """Return grid cell data for rendering the maze as HTML."""
    grid_data = DATA.get_maze_grid(maze_id)
    if not grid_data:
        return jsonify({"error": "No grid data available"}), 404
    return jsonify(grid_data)


@app.route("/api/alternates/<word>")
def get_alternates(word):
    """Get maze-aware alternate words that fit at the given position."""
    maze_id = request.args.get('maze_id', '')
    word_num = request.args.get('word_num', '')
    direction = request.args.get('direction', '')

    if maze_id and word_num and direction:
        alts, constraints = DATA.get_maze_alternates(maze_id, word_num, direction)
        return jsonify({
            "word": word,
            "syllables": DATA._telugu_syllable_count(word),
            "count": len(alts),
            "alternates": alts,
            "constraints": constraints,
            "maze_aware": True,
        })
    else:
        alts = DATA.get_alternates(word)
        return jsonify({
            "word": word,
            "syllables": DATA._telugu_syllable_count(word),
            "count": len(alts),
            "alternates": alts,
            "maze_aware": False,
        })


@app.route("/api/check_word/<word>/<current_maze>")
def check_word(word, current_maze):
    """Check if a word exists in other mazes."""
    mazes = DATA.word_to_mazes.get(word, set())
    other_mazes = sorted([m for m in mazes if m != current_maze], key=int)
    return jsonify({"word": word, "found_in_mazes": other_mazes})


@app.route("/api/slot_alternates")
def slot_alternates():
    """Get alternate words that fit at a specific (possibly empty) slot.
    Query params: maze_id, word_num, direction.
    Works for empty slots (no current word required)."""
    maze_id = request.args.get('maze_id', '')
    word_num = request.args.get('word_num', '')
    direction = request.args.get('direction', '')
    if not (maze_id and word_num and direction):
        return jsonify({"error": "missing params"}), 400
    alts, constraints = DATA.get_maze_alternates(maze_id, word_num, direction)
    # Find slot length for display
    slot_len = 0
    for pos in DATA.maze_positions.get(maze_id, []):
        if str(pos['wordnumber']) == str(word_num) and pos['direction'] == direction:
            slot_len = pos['length']
            break
    return jsonify({
        "maze_id": maze_id,
        "word_num": word_num,
        "direction": direction,
        "syllables": slot_len,
        "count": len(alts),
        "alternates": alts,
        "constraints": constraints,
    })


@app.route("/api/update_word", methods=["POST"])
def update_word():
    """Update a word in a maze and persist to CSV.
    Optional: wordnumber, direction, orient ('fwd'|'rev') to control reverse flag.
    """
    body = request.get_json()
    maze_id = body.get("maze_id")
    old_word = body.get("old_word")
    new_word = body.get("new_word")
    wordnumber = body.get("wordnumber")
    direction = body.get("direction")
    orient = body.get("orient")

    if not maze_id or not new_word or old_word is None:
        return jsonify({"error": "Missing fields"}), 400

    if new_word in DATA.blacklist:
        return jsonify({"error": "Word is blacklisted (marked invalid)"}), 400

    success = DATA.update_word(maze_id, old_word, new_word,
                               wordnumber=wordnumber, direction=direction, orient=orient)

    # Check cross-maze matches for the new word
    other_mazes = sorted(
        [m for m in DATA.word_to_mazes.get(new_word, set()) if m != maze_id], key=int
    )

    return jsonify({
        "success": success,
        "new_word": new_word,
        "found_in_mazes": other_mazes,
    })


@app.route("/api/delete_word", methods=["POST"])
def delete_word():
    """Mark a word slot as empty in a maze. The word becomes available
    again for use in other mazes; this maze tracks an empty slot."""
    body = request.get_json()
    maze_id = body.get("maze_id")
    wordnumber = body.get("wordnumber")
    direction = body.get("direction")
    if not all([maze_id, wordnumber is not None, direction]):
        return jsonify({"error": "Missing fields"}), 400
    deleted = DATA.delete_word(maze_id, wordnumber, direction)
    if deleted is None:
        return jsonify({"success": False, "error": "slot not found"}), 404
    return jsonify({
        "success": True,
        "deleted_word": deleted,
        "still_in_mazes": sorted(list(DATA.word_to_mazes.get(deleted, set())), key=int),
    })


@app.route("/api/empty_slots")
def empty_slots():
    """List all empty slots across all mazes."""
    slots = DATA.get_empty_slots()
    by_maze = {}
    for s in slots:
        by_maze.setdefault(s["maze_id"], []).append(s)
    summary = [
        {"maze_id": mid, "count": len(items), "slots": items}
        for mid, items in sorted(by_maze.items(), key=lambda kv: int(kv[0]))
    ]
    return jsonify({"total": len(slots), "mazes": summary})


@app.route("/api/mark_invalid", methods=["POST"])
def mark_invalid():
    """Mark a word as invalid: removes it from any maze using it and from
    all alternate / suggestion sources."""
    body = request.get_json()
    word = (body.get("word") or "").strip()
    if not word:
        return jsonify({"error": "Missing word"}), 400
    result = DATA.mark_invalid(word)
    return jsonify({"success": True, **result})


@app.route("/api/blacklist")
def blacklist_list():
    """Return the current blacklist."""
    items = sorted(DATA.blacklist)
    return jsonify({"count": len(items), "words": items})


@app.route("/api/unblacklist", methods=["POST"])
def unblacklist():
    """Remove a word from the blacklist and restore it to the available pool."""
    body = request.get_json()
    word = (body.get("word") or "").strip()
    if not word:
        return jsonify({"error": "Missing word"}), 400
    result = DATA.unmark_invalid(word)
    return jsonify({"success": True, **result})


@app.route("/api/update_hint", methods=["POST"])
def update_hint():
    """Update hint for a word in the dictionary."""
    body = request.get_json()
    word = body.get("word")
    hint_tel = body.get("hint_tel", "")
    hint_eng = body.get("hint_eng")

    if not word:
        return jsonify({"error": "Missing word"}), 400

    success = DATA.update_hint(word, hint_tel, hint_eng)
    return jsonify({"success": success})


@app.route("/api/update_meaning", methods=["POST"])
def update_meaning():
    """Update meaning for a word in the dictionary."""
    body = request.get_json()
    word = body.get("word")
    meaning = body.get("meaning", "")

    if not word:
        return jsonify({"error": "Missing word"}), 400

    success = DATA.update_meaning(word, meaning)
    return jsonify({"success": success})


@app.route("/api/suggest")
def suggest():
    """Get word suggestions matching length and prefix."""
    current = request.args.get("current", "")
    prefix = request.args.get("prefix", "")
    if not current:
        return jsonify([])
    results = DATA.suggest_words(current, prefix)
    return jsonify(results)


@app.route("/api/reload", methods=["POST"])
def reload_data():
    """Reload all data from disk (useful after external changes)."""
    DATA.reload()
    return jsonify({"success": True, "maze_count": len(DATA.maze_ids)})


@app.route("/api/crossings")
def crossings():
    """List words that cross the given word in a maze.
    Query params: maze_id, word_num, direction."""
    maze_id = request.args.get('maze_id', '')
    word_num = request.args.get('word_num', '')
    direction = request.args.get('direction', '')
    if not (maze_id and word_num and direction):
        return jsonify({"error": "missing params"}), 400
    items = DATA.find_crossing_words(maze_id, word_num, direction)
    return jsonify({"crossings": items, "count": len(items)})


@app.route("/api/pair_alternates")
def pair_alternates():
    """Get alternate (w1', w2') pairs for two crossing words.
    Query params: maze_id, w1_num, w1_dir, w2_num, w2_dir."""
    maze_id = request.args.get('maze_id', '')
    w1_num = request.args.get('w1_num', '')
    w1_dir = request.args.get('w1_dir', '')
    w2_num = request.args.get('w2_num', '')
    w2_dir = request.args.get('w2_dir', '')
    if not all([maze_id, w1_num, w1_dir, w2_num, w2_dir]):
        return jsonify({"error": "missing params"}), 400
    result = DATA.get_maze_pair_alternates(maze_id, w1_num, w1_dir, w2_num, w2_dir)
    return jsonify(result)


@app.route("/api/update_word_pair", methods=["POST"])
def update_word_pair():
    """Apply a pair of word replacements atomically (best-effort: sequential).
    Each replacement may include wordnumber, direction, orient ('fwd'|'rev')
    to control the slot's is_reverse flag.
    """
    body = request.get_json()
    maze_id = body.get("maze_id")
    pairs = body.get("replacements", [])  # [{old, new, wordnumber?, direction?, orient?}, ...]
    if not maze_id or len(pairs) != 2:
        return jsonify({"error": "expected maze_id + 2 replacements"}), 400
    results = []
    for p in pairs:
        ok = DATA.update_word(
            maze_id, p.get("old"), p.get("new"),
            wordnumber=p.get("wordnumber"),
            direction=p.get("direction"),
            orient=p.get("orient"),
        )
        results.append({"old": p.get("old"), "new": p.get("new"), "success": ok})
    return jsonify({"success": all(r["success"] for r in results), "results": results})


@app.route("/api/toggle_mark", methods=["POST"])
def toggle_mark():
    """Toggle marked state for a word."""
    body = request.get_json()
    maze_id = body.get("maze_id")
    wordnumber = body.get("wordnumber")
    word = body.get("word")
    if not all([maze_id, wordnumber, word]):
        return jsonify({"error": "Missing fields"}), 400
    marked = DATA.toggle_mark_word(maze_id, wordnumber, word)
    return jsonify({"success": True, "marked": marked})


@app.route("/api/marked_words")
def get_marked_words():
    """Get all marked words with maze info."""
    words = DATA.get_marked_words_list()
    return jsonify({"words": words, "count": len(words)})


@app.route("/api/is_marked")
def is_marked():
    """Check if specific words are marked. Pass maze_id to get marks for that maze."""
    maze_id = request.args.get('maze_id', '')
    if not maze_id:
        return jsonify({})
    marks = {}
    for mid, wnum, word in DATA.marked_words:
        if mid == maze_id:
            marks[f"{wnum}_{word}"] = True
    return jsonify(marks)


@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(DATA.puzzle_dir, filename)


def main():
    global PUZZLE_DIR, DATA

    if len(sys.argv) < 2:
        print(__doc__)
        print("ERROR: Please provide the puzzle folder path as an argument.")
        print("Example: python server.py c:\\myprograms\\Book1")
        sys.exit(1)

    PUZZLE_DIR = os.path.abspath(sys.argv[1])
    if not os.path.isdir(PUZZLE_DIR):
        print(f"ERROR: Folder not found: {PUZZLE_DIR}")
        sys.exit(1)

    words_csv = os.path.join(PUZZLE_DIR, "SelectedWords.csv")
    if not os.path.exists(words_csv):
        print(f"ERROR: SelectedWords.csv not found in {PUZZLE_DIR}")
        sys.exit(1)

    DATA = PuzzleData(PUZZLE_DIR)
    print(f"Puzzle folder: {PUZZLE_DIR}")
    print(f"Loaded {len(DATA.maze_ids)} mazes, {len(DATA.word_info)} dictionary words")
    print(f"Word index: {len(DATA.word_to_mazes)} unique words across all mazes")
    print(f"Available words for suggestions: {len(DATA.available_words)}")
    print(f"Images found: {len(DATA.image_map)}")
    print(f"\nOpen http://127.0.0.1:5000 in your browser")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
