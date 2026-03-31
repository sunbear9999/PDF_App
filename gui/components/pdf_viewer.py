import customtkinter as ctk
import fitz  # PyMuPDF
from PIL import Image, ImageTk
import io
from gui.components.annotation_manager import AnnotationManager

class PDFViewer(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        
        self.doc = None
        self.base_zoom = 1.0          
        self.user_zoom_modifier = 1.0 
        self.view_mode = "read"       
        
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        self.canvas_frame = ctk.CTkScrollableFrame(self, fg_color="#1e1e1e", corner_radius=0)
        self.canvas_frame.grid(row=0, column=0, sticky="nsew")
        
        self._bind_mouse_scroll(self.canvas_frame)
        self._bind_mouse_scroll(self.canvas_frame._parent_canvas)
        
        self.image_labels = [] 
        self._resize_timer = None
        self._current_width = 0
        self._render_id = 0 
        
        self.canvas_frame.bind("<Configure>", self._on_resize)
        self.annot_manager = AnnotationManager(self)

    @property
    def on_note_added(self): return self.annot_manager.on_note_added
    @on_note_added.setter
    def on_note_added(self, func): self.annot_manager.on_note_added = func

    def toggle_search(self): self.annot_manager.toggle_search()
    def close_search(self): self.annot_manager.close_search()
    def delete_note(self, page_num, note_id, fallback_data): self.annot_manager.delete_note(page_num, note_id, fallback_data)
    def change_note_color(self, page_num, note_id, fallback_data, r, g, b): self.annot_manager.change_note_color(page_num, note_id, fallback_data, r, g, b)

    def show_toast(self, message, color="#ffaa00"):
        """Displays a sleek, temporary notification bubble at the bottom center of the screen."""
        if hasattr(self, 'toast') and self.toast.winfo_exists():
            self.toast.destroy()
            
        self.toast = ctk.CTkLabel(
            self.winfo_toplevel(), text=message, fg_color=color, text_color="white", 
            corner_radius=8, height=35, font=ctk.CTkFont(weight="bold", size=13)
        )
        # Pad inner text nicely
        self.toast.pack_propagate(False)
        self.toast.configure(width=self.toast.winfo_reqwidth() + 40)
        
        self.toast.place(relx=0.5, rely=0.9, anchor="center")
        self.after(3000, lambda: self.toast.place_forget() if self.toast.winfo_exists() else None)

    def get_current_zoom(self):
        return self.base_zoom * self.user_zoom_modifier

    def _bind_mouse_scroll(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel)
        widget.bind("<Button-4>", self._on_mousewheel)
        widget.bind("<Button-5>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        if event.num == 4: direction = -1
        elif event.num == 5: direction = 1
        elif event.delta: direction = -1 if event.delta > 0 else 1
        else: return
        self.canvas_frame._parent_canvas.yview_scroll(direction, "units")

    def _on_resize(self, event):
        if abs(event.width - self._current_width) > 20:
            self._current_width = event.width
            if self._resize_timer:
                self.after_cancel(self._resize_timer)
            # CRITICAL FIX: Trigger in-place resize to prevent scroll jump!
            self._resize_timer = self.after(300, self._resize_in_place)

    def _resize_in_place(self):
        if not self.doc or self.view_mode != "read": return
        page0 = self.doc.load_page(0)
        target_width = max(200, self._current_width - 40)
        self.base_zoom = target_width / page0.rect.width
        # Queue redraws for existing canvases so memory and scroll position remain perfectly intact
        self.queue_page_redraws(list(range(len(self.image_labels))))

    def load_document(self, pdf_path):
        if self.doc: self.doc.close()
        try:
            self.doc = fitz.open(pdf_path)
            self.user_zoom_modifier = 1.0 
            self.annot_manager.close_search()
            self.annot_manager.popup.place_forget()
            if self._current_width < 100:
                self.update_idletasks()
                self._current_width = self.canvas_frame.winfo_width()
            self.render_view()
            return True
        except Exception as e:
            self.show_toast(f"Failed to load PDF: {e}", "#ff4444")
            return False

    def close_document(self):
        if self.doc:
            self.doc.close()
            self.doc = None
        self.clear_canvas()
        self.annot_manager.close_search()
        self.annot_manager.popup.place_forget()

    def clear_canvas(self):
        self._render_id += 1 
        for child in self.canvas_frame.winfo_children():
            child.destroy()
        self.image_labels.clear()
        self.update_idletasks()

    def set_view_mode(self, mode):
        self.view_mode = mode
        self.render_view()

    def render_view(self):
        if not self.doc: return
        self.clear_canvas()
        current_render_id = self._render_id

        if self.view_mode == "read":
            self._render_continuous(current_render_id)
        elif self.view_mode == "overview":
            self._render_overview_grid(current_render_id)

    def _render_continuous(self, render_id):
        if not self.doc: return
        page0 = self.doc.load_page(0)
        target_width = max(200, self._current_width - 40) 
        self.base_zoom = target_width / page0.rect.width
        final_zoom = self.base_zoom * self.user_zoom_modifier
        self._render_page_step(0, final_zoom, render_id)

    def _render_page_step(self, page_num, zoom, render_id):
        if render_id != self._render_id: return 
        if not self.doc or page_num >= len(self.doc) or self.view_mode != "read": return

        page = self.doc.load_page(page_num)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        tk_img = ImageTk.PhotoImage(img)
        
        canvas = ctk.CTkCanvas(self.canvas_frame, width=img.width, height=img.height, bg="#1e1e1e", highlightthickness=0)
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.image = tk_img 
        canvas.pack(pady=10)
        
        self._bind_mouse_scroll(canvas)
        self.annot_manager.bind_canvas_events(canvas, page_num)
        
        self.image_labels.append(canvas)
        self.canvas_frame._parent_canvas.configure(scrollregion=self.canvas_frame._parent_canvas.bbox("all"))
        self.after(5, self._render_page_step, page_num + 1, zoom, render_id)

    def _render_overview_grid(self, render_id):
        columns = 4
        thumbnail_zoom = 0.2
        row_frame = None
        for i in range(len(self.doc)):
            if render_id != self._render_id: return
            if i % columns == 0:
                row_frame = ctk.CTkFrame(self.canvas_frame, fg_color="transparent")
                row_frame.pack(pady=10, fill="x")
                self._bind_mouse_scroll(row_frame)
                
            page = self.doc.load_page(i)
            mat = fitz.Matrix(thumbnail_zoom, thumbnail_zoom)
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
            lbl = ctk.CTkLabel(row_frame, text=f"Pg {i+1}", image=ctk_img, compound="top")
            lbl.pack(side="left", padx=10, expand=True)
            self._bind_mouse_scroll(lbl)
            
            self.canvas_frame._parent_canvas.configure(scrollregion=self.canvas_frame._parent_canvas.bbox("all"))

    def redraw_page(self, page_num):
        if page_num >= len(self.image_labels): return
        canvas = self.image_labels[page_num]
        
        page = self.doc.load_page(page_num)
        zoom = self.get_current_zoom()
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        tk_img = ImageTk.PhotoImage(img)
        
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        if hasattr(canvas, 'image'): del canvas.image 
        canvas.image = tk_img 
        canvas.config(width=img.width, height=img.height)

    def queue_page_redraws(self, pages_list):
        if not pages_list: return
        chunk = pages_list[:2]
        remaining = pages_list[2:]
        for p in chunk: self.redraw_page(p)
        if remaining: self.after(10, self.queue_page_redraws, remaining)

    def jump_to_page(self, target_page):
        fraction = target_page / len(self.doc)
        self.canvas_frame._parent_canvas.yview_moveto(fraction)
        
    def _jump_to_page(self, target_page):
        self.jump_to_page(target_page)

    def zoom_in(self):
        self.user_zoom_modifier += 0.2
        self.render_view()

    def zoom_out(self):
        self.user_zoom_modifier = max(0.4, self.user_zoom_modifier - 0.2)
        self.render_view()
        
    def zoom_reset(self):
        self.user_zoom_modifier = 1.0
        self.render_view()