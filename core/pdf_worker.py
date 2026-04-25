# core/pdf_worker.py
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextDocument, QPdfWriter, QPageSize, QPageLayout
from PySide6.QtCore import QSizeF, QMarginsF

def main():
    if len(sys.argv) < 3:
        sys.exit(1)
        
    html_path = sys.argv[1]
    pdf_path = sys.argv[2]
    
    # 1. Spin up a completely isolated, headless Qt application
    app = QApplication(sys.argv)
    
    # 2. Read the raw HTML
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
        
    # 3. Render the PDF
    doc = QTextDocument()
    doc.setHtml(html)
    
    writer = QPdfWriter(pdf_path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
    doc.setPageSize(QSizeF(794, 1122))
    
    doc.print_(writer)
    
    # 4. Self-destruct the isolated process
    sys.exit(0)

if __name__ == "__main__":
    main()