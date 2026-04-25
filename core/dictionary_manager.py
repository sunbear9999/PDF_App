# core/dictionary_manager.py
import sqlite3
import json
import csv
import os
import uuid
import unicodedata

class DictionaryManager:
    def __init__(self, app_data_dir):
        self.app_data_dir = app_data_dir
        os.makedirs(self.app_data_dir, exist_ok=True)
        
        self.db_filepath = os.path.join(self.app_data_dir, "papyrus_dictionaries.db")
        self._conn = None
        
        self._init_db()
        self._repair_database()

    def _init_db(self):
        try:
            self._conn = sqlite3.connect(self.db_filepath, check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            
            cursor = self._conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS dictionaries (
                id TEXT PRIMARY KEY, 
                name TEXT, 
                format TEXT, 
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Removed the has_embedding column entirely
            cursor.execute('''CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY, 
                dict_id TEXT, 
                word TEXT, 
                normalized_word TEXT, 
                definition TEXT, 
                FOREIGN KEY(dict_id) REFERENCES dictionaries(id) ON DELETE CASCADE
            )''')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_word ON entries(word)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_normalized_word ON entries(normalized_word)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_dict_id ON entries(dict_id)')
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"[Dictionary] Database initialization error: {e}")

    # --- UNIVERSAL TEXT CLEANERS ---

    def strip_punctuation(self, word):
        """Universally removes formatting punctuation (periods, hyphens) but keeps letters and diacritics."""
        if not word: return ""
        text = str(word).strip()
        for char in ['.', '-', ',', ';', ':', '·', '|']:
            text = text.replace(char, '')
        return text

    def normalize_word(self, word):
        """Aggressively strips diacritics, okina, and punctuation for fuzzy matching."""
        if not word: return ""
        text = self.strip_punctuation(word).lower()
        for char in ['ʻ', '`', "'", '‘', '’']:
            text = text.replace(char, '')
        text = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
        return text

    def _repair_database(self):
        """Silently scrubs punctuation out of existing database entries on launch."""
        if not self._conn: return
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, word, normalized_word FROM entries")
            updates = []
            for r_id, word, norm in cursor.fetchall():
                new_word = self.strip_punctuation(word)
                new_norm = self.normalize_word(word)
                if new_word != word or new_norm != norm:
                    updates.append((new_word, new_norm, r_id))
                    
            if updates:
                print(f"[Dictionary] Applying universal formatting repair to {len(updates)} entries...")
                cursor.executemany("UPDATE entries SET word = ?, normalized_word = ? WHERE id = ?", updates)
                self._conn.commit()
                print("[Dictionary] Repair complete! Search is now fully optimized.")
        except Exception as e:
            print(f"[Dictionary] Repair failed: {e}")

    def get_word_variations(self, word):
        """A lightweight stemmer to catch plurals and suffixes."""
        word = str(word).lower().strip()
        variations = [word]
        suffixes = {'s': 1, 'es': 2, 'ing': 3, 'ed': 2, 'ly': 2, 'er': 2, 'est': 3}
        
        for suf, drop in suffixes.items():
            if word.endswith(suf) and len(word) > drop + 2: 
                base = word[:-drop]
                variations.append(base)
                if base.endswith('i'): variations.append(base[:-1] + 'y') 
                variations.append(base + 'e') 
                if len(base) > 1 and base[-1] == base[-2]:
                    variations.append(base[:-1]) 
                    
        return list(set(variations))

    # --- QUERY LOGIC ---

    def exact_search(self, query, dict_id=None, ignore_diacritics=True):
        if not self._conn or not query: return []
        
        variations = self.get_word_variations(query)
        
        try:
            cursor = self._conn.cursor()
            
            if ignore_diacritics:
                search_terms = [self.normalize_word(v) for v in variations]
                column = "normalized_word"
            else:
                search_terms = [self.strip_punctuation(v) for v in variations]
                column = "word"
                
            placeholders = ",".join("?" * len(search_terms))
            sql = f"SELECT e.word, e.definition, d.name FROM entries e JOIN dictionaries d ON e.dict_id = d.id WHERE e.{column} IN ({placeholders}) COLLATE NOCASE"
            params = search_terms[:]
            
            if dict_id and dict_id != "ALL":
                sql += " AND e.dict_id = ?"
                params.append(dict_id)
                
            cursor.execute(sql, params)
            results = []
            for row in cursor.fetchall():
                results.append({
                    "word": row[0],
                    "definition": row[1],
                    "dictionary": row[2]
                })
            return results
        except sqlite3.Error as e:
            print(f"[Dictionary] Search error: {e}")
            return []

    # --- DICTIONARY MANAGEMENT ---

    def get_available_dictionaries(self):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, name FROM dictionaries ORDER BY name")
            return [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def _register_dictionary(self, name, fmt):
        dict_id = f"dict_{uuid.uuid4().hex[:8]}"
        try:
            self._conn.execute("INSERT INTO dictionaries (id, name, format) VALUES (?, ?, ?)", (dict_id, name, fmt))
            self._conn.commit()
            return dict_id
        except sqlite3.Error as e:
            print(f"[Dictionary] Failed to register dictionary: {e}")
            return None

    def add_custom_entry(self, dict_id, word, definition):
        """Manually adds a custom word and definition to a specific dictionary."""
        if not self._conn or not dict_id or not word or not definition: return False
        
        entry_id = f"ent_{uuid.uuid4().hex}"
        clean_word = self.strip_punctuation(word)
        normalized = self.normalize_word(word)
        
        try:
            self._conn.execute(
                "INSERT INTO entries (id, dict_id, word, normalized_word, definition) VALUES (?, ?, ?, ?, ?)",
                (entry_id, dict_id, clean_word, normalized, definition)
            )
            self._conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"[Dictionary] Failed to add custom entry: {e}")
            return False

    def _bulk_insert_entries(self, dict_id, entries_list):
        if not self._conn or not entries_list: return
        
        db_entries = []
        for word, definition in entries_list:
            if not word or not definition: continue
            entry_id = f"ent_{uuid.uuid4().hex}"
            
            clean_word = self.strip_punctuation(word)
            normalized = self.normalize_word(word)
            
            db_entries.append((entry_id, dict_id, clean_word, normalized, definition))

        try:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            cursor.executemany(
                "INSERT INTO entries (id, dict_id, word, normalized_word, definition) VALUES (?, ?, ?, ?, ?)",
                db_entries
            )
            self._conn.commit()
            print(f"[Dictionary] Successfully imported {len(db_entries)} entries.")
        except sqlite3.Error as e:
            self._conn.rollback()
            print(f"[Dictionary] Bulk insert failed: {e}")

    # --- IMPORTERS ---
    
    def import_json(self, filepath, dict_name=None):
        if not dict_name:
            dict_name = os.path.basename(filepath).replace(".json", "")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            entries = []
            if isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, list):
                        val = "\n".join(f"• {str(v)}" for v in val)
                    elif isinstance(val, dict):
                        val = val.get("definition") or val.get("definitions") or val.get("meaning") or str(val)
                        if isinstance(val, list): val = "\n".join(f"• {str(v)}" for v in val)
                    if key and val: entries.append((str(key).strip(), str(val).strip()))
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        w = item.get("word") or item.get("term") or item.get("id") or item.get("headword")
                        d = item.get("definitions") or item.get("definition") or item.get("meaning") or item.get("meanings") or item.get("desc")
                        if isinstance(d, list):
                            clean_defs = [str(v).strip() for v in d if str(v).strip()]
                            d = "<br>".join(f"• {v}" for v in clean_defs)
                        if w and d: entries.append((str(w).strip(), str(d).strip()))

            if not entries: return False
            dict_id = self._register_dictionary(dict_name, "json")
            if not dict_id: return False
            self._bulk_insert_entries(dict_id, entries)
            return True
        except Exception as e:
            print(f"[Dictionary] JSON import failed: {e}")
            return False

    def import_csv(self, filepath, dict_name=None):
        if not dict_name:
            dict_name = os.path.basename(filepath).replace(".csv", "")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                entries = []
                for row in reader:
                    if len(row) >= 2: entries.append((row[0].strip(), row[1].strip()))
            dict_id = self._register_dictionary(dict_name, "csv")
            if not dict_id: return False
            self._bulk_insert_entries(dict_id, entries)
            return True
        except Exception as e:
            print(f"[Dictionary] CSV import failed: {e}")
            return False

    def import_xdxf(self, filepath, dict_name=None):
        import xml.etree.ElementTree as ET
        if not dict_name:
            dict_name = os.path.basename(filepath).replace(".xdxf", "")
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
            entries = []
            for ar in root.findall('.//ar'):
                k_tag = ar.find('k')
                if k_tag is None or not k_tag.text: continue
                word = k_tag.text.strip()
                defs = []
                for deftext in ar.findall('.//deftext'):
                    if deftext.text and deftext.text.strip(): defs.append(deftext.text.strip())
                if not defs:
                    for d in ar.findall('.//def'):
                        if d.text and d.text.strip(): defs.append(d.text.strip())
                if word and defs:
                    definition_html = "<br>".join(f"• {d}" for d in defs)
                    entries.append((word, definition_html))
            if not entries: return False
            dict_id = self._register_dictionary(dict_name, "xdxf")
            if not dict_id: return False
            self._bulk_insert_entries(dict_id, entries)
            return True
        except Exception as e:
            print(f"[Dictionary] XDXF import failed: {e}")
            return False

    def import_stardict(self, ifo_filepath):
        import struct
        base_path = ifo_filepath[:-4] 
        idx_path = base_path + ".idx"
        dict_path = base_path + ".dict"
        if not (os.path.exists(idx_path) and os.path.exists(dict_path)): return False

        dict_name = os.path.basename(base_path)
        with open(ifo_filepath, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("bookname="):
                    dict_name = line.split("=")[1].strip()
                    break
        try:
            entries = []
            with open(idx_path, 'rb') as f_idx, open(dict_path, 'rb') as f_dict:
                idx_data = f_idx.read()
                i = 0
                while i < len(idx_data):
                    null_idx = idx_data.find(b'\x00', i)
                    if null_idx == -1: break
                    word = idx_data[i:null_idx].decode('utf-8')
                    i = null_idx + 1
                    offset, size = struct.unpack('>II', idx_data[i:i+8])
                    i += 8
                    f_dict.seek(offset)
                    def_bytes = f_dict.read(size)
                    definition = def_bytes.decode('utf-8', errors='ignore').strip()
                    if definition.startswith("<p>") and definition.endswith("</p>"):
                        definition = definition[3:-4]
                    entries.append((word, definition))

            if not entries: return False
            dict_id = self._register_dictionary(dict_name, "stardict")
            if not dict_id: return False
            self._bulk_insert_entries(dict_id, entries)
            return True
        except Exception as e:
            print(f"[Dictionary] StarDict import failed: {e}")
            return False