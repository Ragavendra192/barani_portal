"""
PDF Manager Application with Acrobat Reader ActiveX Integration
A PyQt5 desktop application for managing and viewing PDF files
"""

import sys
import os
import sqlite3
from typing import Optional, List, Tuple
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QFileDialog, QLineEdit, QLabel, QDialog, QFormLayout,
    QDialogButtonBox, QSplitter, QHeaderView
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtAxContainer import QAxWidget


class DatabaseManager:
    """Handles all SQLite database operations for PDF records"""
    
    def __init__(self, db_path: str = "pdf_records.db"):
        """
        Initialize database manager and create tables if needed
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._create_table()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Create and return a database connection"""
        return sqlite3.connect(self.db_path)
    
    def _create_table(self):
        """Create the PDF records table if it doesn't exist"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pdf_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    
    def create_record(self, title: str, file_path: str) -> int:
        """
        Create a new PDF record
        
        Args:
            title: Title/name for the PDF
            file_path: Full path to the PDF file
            
        Returns:
            ID of the newly created record
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO pdf_records (title, file_path) VALUES (?, ?)",
                (title, file_path)
            )
            conn.commit()
            return cursor.lastrowid
    
    def read_all_records(self) -> List[Tuple[int, str, str]]:
        """
        Retrieve all PDF records
        
        Returns:
            List of tuples (id, title, file_path)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, file_path FROM pdf_records ORDER BY created_at DESC")
            return cursor.fetchall()
    
    def read_record(self, record_id: int) -> Optional[Tuple[int, str, str]]:
        """
        Retrieve a specific PDF record by ID
        
        Args:
            record_id: ID of the record to retrieve
            
        Returns:
            Tuple (id, title, file_path) or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, title, file_path FROM pdf_records WHERE id = ?",
                (record_id,)
            )
            return cursor.fetchone()
    
    def update_record(self, record_id: int, title: str, file_path: str) -> bool:
        """
        Update an existing PDF record
        
        Args:
            record_id: ID of the record to update
            title: New title
            file_path: New file path
            
        Returns:
            True if update was successful, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pdf_records SET title = ?, file_path = ? WHERE id = ?",
                (title, file_path, record_id)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def delete_record(self, record_id: int) -> bool:
        """
        Delete a PDF record
        
        Args:
            record_id: ID of the record to delete
            
        Returns:
            True if deletion was successful, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pdf_records WHERE id = ?", (record_id,))
            conn.commit()
            return cursor.rowcount > 0


class PdfViewer(QWidget):
    """Wrapper for Acrobat Reader ActiveX control"""
    
    def __init__(self, parent=None):
        """Initialize the PDF viewer widget"""
        super().__init__(parent)
        self.pdf_widget: Optional[QAxWidget] = None
        self.current_file: Optional[str] = None
        self._init_ui()
    
    def _init_ui(self):
        """Initialize the user interface"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        try:
            # Create ActiveX widget for Acrobat Reader
            self.pdf_widget = QAxWidget("AcroPDF.PDF.1", self)
            layout.addWidget(self.pdf_widget)
        except Exception as e:
            # Fallback if ActiveX control is not available
            error_label = QLabel(
                f"Acrobat Reader ActiveX control not available.\n"
                f"Error: {str(e)}\n\n"
                f"Please install Adobe Acrobat Reader DC.",
                self
            )
            error_label.setAlignment(Qt.AlignCenter)
            error_label.setStyleSheet("color: red; padding: 20px;")
            layout.addWidget(error_label)
    
    def load_pdf(self, file_path: str) -> bool:
        """
        Load a PDF file into the viewer
        
        Args:
            file_path: Path to the PDF file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pdf_widget:
            return False
        
        if not os.path.exists(file_path):
            QMessageBox.warning(
                self,
                "File Not Found",
                f"The PDF file does not exist:\n{file_path}"
            )
            return False
        
        try:
            # Load the PDF using the LoadFile method
            self.pdf_widget.dynamicCall("LoadFile(QString)", file_path)
            self.current_file = file_path
            return True
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Loading PDF",
                f"Failed to load PDF file:\n{str(e)}"
            )
            return False
    
    def clear_viewer(self):
        """Clear the current PDF from the viewer"""
        if self.pdf_widget:
            try:
                # Clear the current document
                self.pdf_widget.clear()
                self.current_file = None
            except Exception:
                pass


class PdfRecordDialog(QDialog):
    """Dialog for creating or editing PDF records"""
    
    def __init__(self, parent=None, title: str = "", file_path: str = "", edit_mode: bool = False):
        """
        Initialize the dialog
        
        Args:
            parent: Parent widget
            title: Initial title value
            file_path: Initial file path value
            edit_mode: True if editing existing record, False if creating new
        """
        super().__init__(parent)
        self.title_edit = QLineEdit()
        self.path_edit = QLineEdit()
        self.browse_button = QPushButton("Browse...")
        
        self.setWindowTitle("Edit PDF Record" if edit_mode else "Add PDF Record")
        self._init_ui(title, file_path)
    
    def _init_ui(self, title: str, file_path: str):
        """Initialize the user interface"""
        layout = QFormLayout(self)
        
        # Title field
        self.title_edit.setText(title)
        self.title_edit.setPlaceholderText("Enter PDF title...")
        layout.addRow("Title:", self.title_edit)
        
        # File path field with browse button
        path_layout = QHBoxLayout()
        self.path_edit.setText(file_path)
        self.path_edit.setPlaceholderText("Select PDF file...")
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(self.browse_button)
        layout.addRow("File Path:", path_layout)
        
        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addRow(button_box)
        
        # Connect browse button
        self.browse_button.clicked.connect(self._browse_file)
        
        self.setMinimumWidth(500)
    
    def _browse_file(self):
        """Open file browser to select PDF file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select PDF File",
            "",
            "PDF Files (*.pdf);;All Files (*.*)"
        )
        if file_path:
            self.path_edit.setText(file_path)
            # Auto-populate title if empty
            if not self.title_edit.text():
                file_name = os.path.basename(file_path)
                title = os.path.splitext(file_name)[0]
                self.title_edit.setText(title)
    
    def get_values(self) -> Tuple[str, str]:
        """
        Get the entered values
        
        Returns:
            Tuple of (title, file_path)
        """
        return self.title_edit.text().strip(), self.path_edit.text().strip()


class MainWindow(QMainWindow):
    """Main application window integrating all components"""
    
    def __init__(self):
        """Initialize the main window"""
        super().__init__()
        self.db_manager = DatabaseManager()
        self.pdf_viewer = PdfViewer()
        self.table_widget = QTableWidget()
        
        self.setWindowTitle("PDF Manager - Acrobat Reader Integration")
        self.setGeometry(100, 100, 1200, 700)
        
        self._init_ui()
        self._load_records()
    
    def _init_ui(self):
        """Initialize the user interface"""
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)
        
        # Left panel - Controls and table
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        # Control buttons
        button_layout = QHBoxLayout()
        add_button = QPushButton("Add PDF")
        edit_button = QPushButton("Edit")
        delete_button = QPushButton("Delete")
        refresh_button = QPushButton("Refresh")
        
        add_button.clicked.connect(self._add_record)
        edit_button.clicked.connect(self._edit_record)
        delete_button.clicked.connect(self._delete_record)
        refresh_button.clicked.connect(self._load_records)
        
        button_layout.addWidget(add_button)
        button_layout.addWidget(edit_button)
        button_layout.addWidget(delete_button)
        button_layout.addWidget(refresh_button)
        button_layout.addStretch()
        
        left_layout.addLayout(button_layout)
        
        # Table widget
        self.table_widget.setColumnCount(3)
        self.table_widget.setHorizontalHeaderLabels(["ID", "Title", "File Path"])
        self.table_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.setSelectionMode(QTableWidget.SingleSelection)
        self.table_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_widget.itemSelectionChanged.connect(self._on_selection_changed)
        
        left_layout.addWidget(self.table_widget)
        
        # Right panel - PDF Viewer
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        viewer_label = QLabel("PDF Viewer")
        viewer_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(viewer_label)
        right_layout.addWidget(self.pdf_viewer)
        
        # Add panels to splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([400, 800])
        
        main_layout.addWidget(splitter)
    
    def _load_records(self):
        """Load all PDF records into the table"""
        records = self.db_manager.read_all_records()
        
        self.table_widget.setRowCount(0)
        for row_idx, (record_id, title, file_path) in enumerate(records):
            self.table_widget.insertRow(row_idx)
            self.table_widget.setItem(row_idx, 0, QTableWidgetItem(str(record_id)))
            self.table_widget.setItem(row_idx, 1, QTableWidgetItem(title))
            self.table_widget.setItem(row_idx, 2, QTableWidgetItem(file_path))
    
    def _add_record(self):
        """Add a new PDF record"""
        dialog = PdfRecordDialog(self, edit_mode=False)
        if dialog.exec_() == QDialog.Accepted:
            title, file_path = dialog.get_values()
            
            if not title or not file_path:
                QMessageBox.warning(
                    self,
                    "Invalid Input",
                    "Please enter both title and file path."
                )
                return
            
            if not os.path.exists(file_path):
                response = QMessageBox.question(
                    self,
                    "File Not Found",
                    f"The file does not exist:\n{file_path}\n\nDo you want to save the record anyway?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if response == QMessageBox.No:
                    return
            
            try:
                self.db_manager.create_record(title, file_path)
                self._load_records()
                QMessageBox.information(
                    self,
                    "Success",
                    "PDF record added successfully!"
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Database Error",
                    f"Failed to add record:\n{str(e)}"
                )
    
    def _edit_record(self):
        """Edit the selected PDF record"""
        selected_row = self.table_widget.currentRow()
        if selected_row < 0:
            QMessageBox.warning(
                self,
                "No Selection",
                "Please select a record to edit."
            )
            return
        
        record_id = int(self.table_widget.item(selected_row, 0).text())
        current_title = self.table_widget.item(selected_row, 1).text()
        current_path = self.table_widget.item(selected_row, 2).text()
        
        dialog = PdfRecordDialog(
            self,
            title=current_title,
            file_path=current_path,
            edit_mode=True
        )
        
        if dialog.exec_() == QDialog.Accepted:
            title, file_path = dialog.get_values()
            
            if not title or not file_path:
                QMessageBox.warning(
                    self,
                    "Invalid Input",
                    "Please enter both title and file path."
                )
                return
            
            try:
                self.db_manager.update_record(record_id, title, file_path)
                self._load_records()
                
                # Reload PDF if it's currently displayed
                if self.table_widget.currentRow() >= 0:
                    self._on_selection_changed()
                
                QMessageBox.information(
                    self,
                    "Success",
                    "PDF record updated successfully!"
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Database Error",
                    f"Failed to update record:\n{str(e)}"
                )
    
    def _delete_record(self):
        """Delete the selected PDF record"""
        selected_row = self.table_widget.currentRow()
        if selected_row < 0:
            QMessageBox.warning(
                self,
                "No Selection",
                "Please select a record to delete."
            )
            return
        
        record_id = int(self.table_widget.item(selected_row, 0).text())
        title = self.table_widget.item(selected_row, 1).text()
        
        response = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete the record:\n'{title}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if response == QMessageBox.Yes:
            try:
                self.db_manager.delete_record(record_id)
                self.pdf_viewer.clear_viewer()
                self._load_records()
                QMessageBox.information(
                    self,
                    "Success",
                    "PDF record deleted successfully!"
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Database Error",
                    f"Failed to delete record:\n{str(e)}"
                )
    
    def _on_selection_changed(self):
        """Handle table selection changes to display PDF"""
        selected_row = self.table_widget.currentRow()
        if selected_row < 0:
            return
        
        file_path = self.table_widget.item(selected_row, 2).text()
        self.pdf_viewer.load_pdf(file_path)


def main():
    """Main application entry point"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern look
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()