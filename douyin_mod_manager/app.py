import sys

from PySide6.QtWidgets import QApplication

from douyin_mod_manager.storage.database import Database
from douyin_mod_manager.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Douyin Mod Manager")

    database = Database.default()
    database.initialize()

    window = MainWindow(database)
    window.resize(1440, 860)
    window.show()

    code = app.exec()
    window.destroy()
    del window
    return code
