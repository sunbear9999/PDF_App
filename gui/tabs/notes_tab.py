import customtkinter as ctk

class NotesTab(ctk.CTkFrame):
    def __init__(self, master, viewer):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.viewer = viewer
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        self.header = ctk.CTkLabel(self, text="Document Notes", font=ctk.CTkFont(size=28, weight="bold"))
        self.header.grid(row=0, column=0, padx=20, pady=(10, 20), sticky="w")
        
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_frame.grid(row=1, column=0, sticky="nsew", padx=10)
        
        self.note_widgets = []

    def refresh_notes(self):
        """Scans the PDF for User Notes and populates the sidebar."""
        for w in self.note_widgets:
            w.destroy()
        self.note_widgets.clear()
        
        if not self.viewer.doc: return
        
        notes_found = False
        for page_num in range(len(self.viewer.doc)):
            page = self.viewer.doc.load_page(page_num)
            for annot in page.annots():
                title = annot.info.get("title", "")
                if annot.type[0] == 8 and title.startswith("UserNote"):
                    notes_found = True
                    comment = annot.info.get("content", "")
                    highlighted_text = annot.info.get("subject", "")
                    
                    # Extract the hidden ID
                    note_id = title.split("|")[1] if "|" in title else ""
                    
                    self._create_note_card(page_num, note_id, highlighted_text, comment)

        if not notes_found:
            lbl = ctk.CTkLabel(self.scroll_frame, text="No notes found.\n\nHold 'Shift' and drag over text\nin the document to add a note.", text_color="gray")
            lbl.pack(pady=50)
            self.note_widgets.append(lbl)

    def _create_note_card(self, page_num, note_id, highlighted, comment):
        card = ctk.CTkFrame(self.scroll_frame, fg_color="#2b2b2b", corner_radius=8)
        card.pack(fill="x", pady=8, padx=5)

        # Top Action Bar
        top_bar = ctk.CTkFrame(card, fg_color="transparent")
        top_bar.pack(fill="x", padx=10, pady=(10, 5))

        lbl_pg = ctk.CTkLabel(top_bar, text=f"Page {page_num + 1}", font=ctk.CTkFont(size=12, weight="bold"), text_color="#00cc66", cursor="hand2")
        lbl_pg.pack(side="left")
        lbl_pg.bind("<Button-1>", lambda e: self.viewer._jump_to_page(page_num))

        btn_del = ctk.CTkButton(
            top_bar, text="✖", width=24, height=24, 
            fg_color="transparent", hover_color="#ff4444", text_color="gray", 
            command=lambda: self._delete_note_wrapper(card, page_num, note_id, (comment, highlighted))
        )
        btn_del.pack(side="right")

        # Colors (Hex code, RGB Tuple)
        colors = [
            ("#ffcc00", (1, 0.8, 0)),     # Yellow
            ("#00cc66", (0, 0.8, 0.4)),   # Green
            ("#3399ff", (0.2, 0.6, 1.0)), # Blue
            ("#ff66b2", (1, 0.4, 0.7))    # Pink
        ]

        for hex_code, rgb_tuple in colors:
            btn_color = ctk.CTkButton(
                top_bar, text="", width=16, height=16, corner_radius=8, 
                fg_color=hex_code, hover_color=hex_code, 
                command=lambda c=rgb_tuple: self.viewer.change_note_color(page_num, note_id, (comment, highlighted), *c)
            )
            btn_color.pack(side="right", padx=3)

        # Body
        lbl_hi = ctk.CTkLabel(card, text=f'"{highlighted}"', font=ctk.CTkFont(size=13, slant="italic"), text_color="gray", wraplength=230, justify="left", cursor="hand2")
        lbl_hi.pack(anchor="w", padx=15, pady=2)
        lbl_hi.bind("<Button-1>", lambda e: self.viewer._jump_to_page(page_num))

        lbl_cm = ctk.CTkLabel(card, text=comment, font=ctk.CTkFont(size=15), text_color="white", wraplength=230, justify="left", cursor="hand2")
        lbl_cm.pack(anchor="w", padx=15, pady=(5, 12))
        lbl_cm.bind("<Button-1>", lambda e: self.viewer._jump_to_page(page_num))
        
        self.note_widgets.append(card)

    def _delete_note_wrapper(self, card_widget, page_num, note_id, fallback_data):
        self.viewer.delete_note(page_num, note_id, fallback_data)
        card_widget.destroy()
        if card_widget in self.note_widgets:
            self.note_widgets.remove(card_widget)