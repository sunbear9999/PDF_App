import customtkinter as ctk
import fitz
import uuid

class AnnotationManager:
    def __init__(self, viewer):
        self.viewer = viewer
        self.on_note_added = None
        
        self.all_matches = []            
        self.pages_with_highlights = set() 
        self.active_annot_index = -1
        self.last_query = ""
        self._search_timer = None

        self.drag_start_x = 0
        self.drag_start_y = 0

        self._build_search_ui()
        self._build_popup_ui()

    def _build_search_ui(self):
        self.search_frame = ctk.CTkFrame(self.viewer, fg_color="#2b2b2b", corner_radius=8, border_width=1, border_color="#444444")
        self.search_entry = ctk.CTkEntry(self.search_frame, placeholder_text="Find in document...", width=200, border_width=0, corner_radius=6, height=30)
        self.search_entry.pack(side="left", padx=(10, 5), pady=8)
        self.search_entry.bind("<KeyRelease>", self._schedule_search)
        self.search_entry.bind("<Return>", lambda e: self.next_result())
        self.search_status = ctk.CTkLabel(self.search_frame, text="", text_color="gray", width=90, font=ctk.CTkFont(size=12))
        self.search_status.pack(side="left", padx=5)
        self.btn_prev_hit = ctk.CTkButton(self.search_frame, text="▲", width=30, height=30, fg_color="#444444", hover_color="#666666", command=self.prev_result)
        self.btn_prev_hit.pack(side="left", padx=2)
        self.btn_next_hit = ctk.CTkButton(self.search_frame, text="▼", width=30, height=30, fg_color="#444444", hover_color="#666666", command=self.next_result)
        self.btn_next_hit.pack(side="left", padx=2)
        self.btn_close_search = ctk.CTkButton(self.search_frame, text="✖", width=30, height=30, fg_color="transparent", hover_color="#ff4444", command=self.close_search)
        self.btn_close_search.pack(side="left", padx=(5, 10))

    def _build_popup_ui(self):
        self.popup = ctk.CTkFrame(self.viewer.winfo_toplevel(), fg_color="#333333", corner_radius=6, border_width=1, border_color="#555555")
        self.popup_label = ctk.CTkLabel(self.popup, text="", wraplength=250, justify="left", font=ctk.CTkFont(size=14), text_color="white")
        self.popup_label.pack(padx=15, pady=15)

    def bind_canvas_events(self, canvas, page_num):
        canvas.bind("<Shift-ButtonPress-1>", lambda e, p=page_num, c=canvas: self._on_drag_start(e, p, c))
        canvas.bind("<Shift-B1-Motion>", lambda e, c=canvas: self._on_drag_motion(e, c))
        canvas.bind("<Shift-ButtonRelease-1>", lambda e, p=page_num, c=canvas: self._on_drag_end(e, p, c))
        canvas.bind("<ButtonPress-1>", lambda e, p=page_num: self._on_click(e, p))

    def delete_note(self, page_num, note_id, fallback_data):
        page = self.viewer.doc.load_page(page_num)
        for annot in page.annots():
            title = annot.info.get("title", "")
            if title.startswith("UserNote"):
                is_match = (note_id in title) or (annot.info.get("content") == fallback_data[0])
                if is_match:
                    page.delete_annot(annot)
                    break
        self.viewer.redraw_page(page_num)

    def change_note_color(self, page_num, note_id, fallback_data, r, g, b):
        page = self.viewer.doc.load_page(page_num)
        for annot in page.annots():
            title = annot.info.get("title", "")
            if title.startswith("UserNote"):
                is_match = (note_id in title) or (annot.info.get("content") == fallback_data[0])
                if is_match:
                    annot.set_colors(stroke=(r, g, b))
                    annot.update()
                    break
        self.viewer.redraw_page(page_num)

    def _on_click(self, event, page_num):
        self.popup.place_forget() 
        zoom = self.viewer.get_current_zoom()
        pdf_x, pdf_y = event.x / zoom, event.y / zoom
        pt = fitz.Point(pdf_x, pdf_y)
        
        page = self.viewer.doc.load_page(page_num)
        for annot in page.annots():
            if annot.type[0] == 8 and annot.info.get("title", "").startswith("UserNote"):
                rect = annot.rect
                padded_rect = fitz.Rect(rect.x0 - 5, rect.y0 - 5, rect.x1 + 5, rect.y1 + 5)
                
                if padded_rect.contains(pt):
                    self.popup_label.configure(text=annot.info.get("content", ""))
                    root = self.viewer.winfo_toplevel()
                    x = event.x_root - root.winfo_rootx() + 15
                    y = event.y_root - root.winfo_rooty() + 15
                    self.popup.place(x=x, y=y)
                    self.popup.lift() 
                    return

    def _on_drag_start(self, event, page_num, canvas):
        self.popup.place_forget()
        canvas.delete("drag_rect") 
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#3399ff", width=2, dash=(4,4), tags="drag_rect")

    def _on_drag_motion(self, event, canvas):
        canvas.coords("drag_rect", self.drag_start_x, self.drag_start_y, event.x, event.y)

    def _on_drag_end(self, event, page_num, canvas):
        canvas.delete("drag_rect")
        self.viewer.update_idletasks() 
        
        x0, y0 = self.drag_start_x, self.drag_start_y
        x1, y1 = event.x, event.y
        if abs(x1 - x0) < 5 and abs(y1 - y0) < 5: return 
        
        zoom = self.viewer.get_current_zoom()
        start_pt = fitz.Point(x0/zoom, y0/zoom)
        end_pt = fitz.Point(x1/zoom, y1/zoom)
        
        page = self.viewer.doc.load_page(page_num)
        
        # CRITICAL FIX: Extract words sequentially in reading order
        words = page.get_text("words", sort=True) 
        if not words:
            self.viewer.show_toast("No selectable text on this page.", "#ff4444")
            return
            
        def dist(pt, w):
            cx, cy = (w[0]+w[2])/2, (w[1]+w[3])/2
            return (pt.x - cx)**2 + (pt.y - cy)**2

        start_idx = min(range(len(words)), key=lambda i: dist(start_pt, words[i]))
        end_idx = min(range(len(words)), key=lambda i: dist(end_pt, words[i]))

        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx

        hit_words = words[start_idx:end_idx+1]
        
        # Prevent massive accidental highlights 
        if len(hit_words) > 100:
            self.viewer.show_toast("Selection too large. Highlight smaller sections.", "#ffaa00")
            return
            
        dialog = ctk.CTkInputDialog(text="Enter a note for this highlight:", title="Add Note")
        comment = dialog.get_input()
        
        if comment:
            extracted_text = " ".join([w[4] for w in hit_words])
            quads = [fitz.Rect(w[:4]).quad for w in hit_words]
            self.viewer.after(150, lambda: self._apply_note_and_render(page_num, quads, extracted_text, comment))

    def _apply_note_and_render(self, page_num, quads, extracted_text, comment):
        try:
            page = self.viewer.doc.load_page(page_num)
            annot = page.add_highlight_annot(quads)
            annot.set_colors(stroke=(0, 0.8, 0.4)) 
            safe_title = f"UserNote|{uuid.uuid4()}"
            annot.set_info(title=safe_title, content=comment, subject=extracted_text)
            annot.update()
            
            self.viewer.redraw_page(page_num)
            if self.on_note_added:
                self.viewer.after(50, self.on_note_added)
        except Exception as e:
            self.viewer.show_toast("Failed to save note.", "#ff4444")

    # =========================================================
    #                   SEARCH LOGIC
    # =========================================================
    def toggle_search(self):
        if not self.viewer.doc: return
        if self.search_frame.winfo_ismapped():
            self.close_search()
        else:
            self.search_frame.place(relx=0.98, rely=0.02, anchor="ne")
            self.search_entry.focus_set()

    def close_search(self):
        self.search_frame.place_forget()
        self.search_entry.delete(0, "end")
        self.search_status.configure(text="")
        self.last_query = ""
        self.clear_search_highlights()

    def _schedule_search(self, event=None):
        if event and event.keysym in ('Up', 'Down', 'Left', 'Right', 'Shift_L', 'Shift_R', 'Return'):
            return
        if self._search_timer:
            self.search_frame.after_cancel(self._search_timer)
        self._search_timer = self.search_frame.after(300, self.perform_search)

    def clear_search_highlights(self):
        if not self.all_matches: return
        for p_num in self.pages_with_highlights:
            page = self.viewer.doc.load_page(p_num)
            for annot in page.annots():
                if annot.type[0] == 8 and annot.info.get("title") == "Search":
                    page.delete_annot(annot)
                    
        pages_to_update = list(self.pages_with_highlights)
        self.all_matches.clear()
        self.pages_with_highlights.clear()
        self.active_annot_index = -1
        self.viewer.queue_page_redraws(pages_to_update)

    def perform_search(self):
        query = self.search_entry.get().strip()
        if query == self.last_query: return
            
        self.clear_search_highlights()
        self.last_query = query
        
        if not query or not self.viewer.doc:
            self.search_status.configure(text="")
            return
            
        self.search_status.configure(text="Searching...", text_color="orange")
        self.search_entry.update_idletasks()
        self._search_batch(0, query)

    def _search_batch(self, start_page, query):
        if not self.viewer.doc or query != self.last_query: return
        
        end_page = min(start_page + 10, len(self.viewer.doc))
        for page_num in range(start_page, end_page):
            page = self.viewer.doc.load_page(page_num)
            rects = page.search_for(query)
            if rects:
                self.pages_with_highlights.add(page_num)
                for rect in rects:
                    self.all_matches.append((page_num, fitz.Rect(rect)))
                    
        if end_page < len(self.viewer.doc):
            self.viewer.after(10, self._search_batch, end_page, query)
        else:
            self._finalize_search()

    def _finalize_search(self):
        if self.all_matches:
            self.active_annot_index = 0
            for p_num in self.pages_with_highlights:
                self._redraw_page_annots(p_num, active_match_idx=0)
            self.viewer.queue_page_redraws(list(self.pages_with_highlights))
            self.search_status.configure(text=f"1/{len(self.all_matches)}", text_color="white")
            self.viewer.jump_to_page(self.all_matches[0][0])
        else:
            self.search_status.configure(text="0 results", text_color="#ff4444")

    def _redraw_page_annots(self, p_num, active_match_idx):
        page = self.viewer.doc.load_page(p_num)
        for annot in page.annots():
            if annot.type[0] == 8 and annot.info.get("title") == "Search":
                page.delete_annot(annot)
                
        for idx, (match_p_num, rect) in enumerate(self.all_matches):
            if match_p_num == p_num:
                annot = page.add_highlight_annot(rect)
                annot.set_info(title="Search")
                if idx == active_match_idx:
                    annot.set_colors(stroke=(1, 0.4, 0)) 
                else:
                    annot.set_colors(stroke=(1, 1, 0)) 
                annot.update()

    def _update_hit_colors(self, old_idx, new_idx):
        pages_to_update = set()
        if 0 <= old_idx < len(self.all_matches):
            pages_to_update.add(self.all_matches[old_idx][0])
        if 0 <= new_idx < len(self.all_matches):
            pages_to_update.add(self.all_matches[new_idx][0])
            
        for p_num in pages_to_update:
            self._redraw_page_annots(p_num, active_match_idx=new_idx)
            self.viewer.redraw_page(p_num)
            
        if 0 <= new_idx < len(self.all_matches):
            target_page = self.all_matches[new_idx][0]
            self.viewer.jump_to_page(target_page)
            self.search_status.configure(text=f"{new_idx + 1}/{len(self.all_matches)}", text_color="white")

    def next_result(self):
        if not self.all_matches: return
        old_idx = self.active_annot_index
        self.active_annot_index = (self.active_annot_index + 1) % len(self.all_matches)
        self._update_hit_colors(old_idx, self.active_annot_index)

    def prev_result(self):
        if not self.all_matches: return
        old_idx = self.active_annot_index
        self.active_annot_index = (self.active_annot_index - 1) % len(self.all_matches)
        self._update_hit_colors(old_idx, self.active_annot_index)