"""
ui/main_window.py — CleanMint Main Window

Sidebar navigation + stacked content pages.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame, QSizePolicy,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon

from ui.theme import Theme
from config.settings import settings
from core.installer import is_policy_installed, install_policy


NAV_ITEMS = [
    ("dashboard",  "⬡  Dashboard"),
    ("cleaner",    "✦  Cleaner"),
    ("analyzer",   "◈  Analyzer"),
    ("apps",       "⊞  Apps"),
    ("startup",    "⚡  Startup"),
    ("health",     "♥  Health"),
    ("snapshot",   "◉  Snapshots"),
    ("settings",   "⚙  Settings"),
    ("logs",       "▤  Logs"),
]


class SidebarButton(QPushButton):
    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setObjectName("SidebarBtn")
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_active(self, active: bool):
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class Sidebar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 20, 12, 20)
        layout.setSpacing(2)

        # Logo / app name
        logo = QLabel("CleanMint")
        logo.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        logo.setStyleSheet(f"color: {Theme.p().accent}; padding: 0 4px 16px 4px;")
        layout.addWidget(logo)

        tagline = QLabel("System Cleaner")
        tagline.setObjectName("MutedLabel")
        tagline.setStyleSheet(f"color: {Theme.p().text_muted}; font-size: 11px; padding: 0 4px 8px 4px;")
        layout.addWidget(tagline)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)
        layout.addSpacing(8)

        self._buttons: dict[str, SidebarButton] = {}
        for key, label in NAV_ITEMS:
            btn = SidebarButton(label)
            layout.addWidget(btn)
            self._buttons[key] = btn

        layout.addStretch()

        # Theme toggle at bottom
        self._theme_btn = QPushButton("☀  Light Mode" if Theme.is_dark() else "☾  Dark Mode")
        self._theme_btn.setObjectName("SecondaryBtn")
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.setFixedHeight(34)
        layout.addWidget(self._theme_btn)

    def button(self, key: str) -> SidebarButton:
        return self._buttons[key]

    @property
    def theme_button(self) -> QPushButton:
        return self._theme_btn


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CleanMint — Linux System Cleaner")
        self.setMinimumSize(960, 640)
        self.resize(1100, 720)

        # Apply theme
        if settings.get("dark_mode", True):
            Theme.set_dark()
        else:
            Theme.set_light()
        self.setStyleSheet(Theme.stylesheet())

        self._build_ui()
        self._connect_signals()
        self._navigate("dashboard")

        # Offer polkit setup on first launch (deferred so window paints first)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(800, self._check_polkit_setup)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        # Vertical separator line
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setStyleSheet(f"color: {Theme.p().border};")
        root.addWidget(line)

        # Content area
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stack, 1)

        # Lazy-load pages
        self._pages: dict[str, QWidget] = {}
        self._load_page("dashboard")

    def _load_page(self, key: str) -> QWidget:
        if key in self._pages:
            return self._pages[key]

        page = self._create_page(key)
        self.stack.addWidget(page)
        self._pages[key] = page
        return page

    def _create_page(self, key: str) -> QWidget:
        """Instantiate the correct page widget for a nav key."""
        if key == "dashboard":
            from ui.dashboard import DashboardPage
            page = DashboardPage()
            page.navigate_to.connect(self._navigate)
            return page
        elif key == "cleaner":
            from ui.cleaner_page import CleanerPage
            return CleanerPage()
        elif key == "analyzer":
            from ui.analyzer_page import AnalyzerPage
            return AnalyzerPage()
        elif key == "apps":
            from ui.apps_page import AppsPage
            return AppsPage()
        elif key == "startup":
            from ui.startup_page import StartupPage
            return StartupPage()
        elif key == "health":
            from ui.health_page import HealthPage
            return HealthPage()
        elif key == "snapshot":
            from ui.snapshot_page import SnapshotPage
            return SnapshotPage()
        elif key == "settings":
            from ui.settings_page import SettingsPage
            return SettingsPage()
        elif key == "logs":
            from ui.logs_page import LogsPage
            return LogsPage()
        else:
            return self._placeholder_page(key)

    def _placeholder_page(self, key: str) -> QWidget:
        """Temporary placeholder for pages not yet implemented."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel(f"{key.capitalize()} — Coming soon")
        label.setObjectName("TitleLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

        sub = QLabel("This section is under construction.")
        sub.setObjectName("SubtitleLabel")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub)
        return page

    def _connect_signals(self):
        for key, _ in NAV_ITEMS:
            btn = self.sidebar.button(key)
            btn.clicked.connect(lambda checked, k=key: self._navigate(k))

        self.sidebar.theme_button.clicked.connect(self._toggle_theme)

    def _navigate(self, key: str):
        # Update active state on all sidebar buttons
        for k, _ in NAV_ITEMS:
            self.sidebar.button(k).set_active(k == key)

        page = self._load_page(key)
        self.stack.setCurrentWidget(page)

    def _check_polkit_setup(self):
        """On first run (or after an update), offer to install/update the polkit policy."""
        if is_policy_installed():
            return

        from pathlib import Path
        dest = Path("/usr/share/polkit-1/actions/org.cleanmint.policy")
        is_update = dest.exists()  # file exists but content differs → update

        # Only skip if user explicitly declined AND this isn't a required update
        if not is_update and settings.get("polkit_setup_declined", False):
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("CleanMint — One-time Setup" if not is_update else "CleanMint — Policy Update")
        msg.setIcon(QMessageBox.Icon.Information)
        if is_update:
            msg.setText(
                "<b>CleanMint policy update available</b><br><br>"
                "The installed policy is outdated. Updating adds support for "
                "cleaning the APT package cache without repeated password prompts.<br><br>"
                "You will be asked for your password <b>once</b>."
            )
        else:
            msg.setText(
                "<b>Enable automatic privilege escalation?</b><br><br>"
                "CleanMint can clean system journal logs, old Snap revisions, and the "
                "APT package cache automatically with a <b>single password prompt per session</b>.<br><br>"
                "This requires installing a small policy file to "
                "<code>/usr/share/polkit-1/actions/</code>. "
                "The app handles this automatically on every machine."
            )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        msg.button(QMessageBox.StandardButton.Yes).setText("Install Policy")
        msg.button(QMessageBox.StandardButton.No).setText("Skip for Now")

        if msg.exec() == QMessageBox.StandardButton.Yes:
            ok, detail = install_policy()
            result_msg = QMessageBox(self)
            result_msg.setWindowTitle("Setup Result")
            if ok:
                result_msg.setIcon(QMessageBox.Icon.Information)
                result_msg.setText(
                    "Policy installed successfully.\n\n"
                    "CleanMint can now clean journal logs and Snap revisions "
                    "with a single password prompt."
                )
            else:
                result_msg.setIcon(QMessageBox.Icon.Warning)
                result_msg.setText(
                    f"Could not install policy automatically.\n\n{detail}\n\n"
                    "You can install it manually by running:\n\n"
                    "  sudo cp ~/.local/share/cleanmint/org.cleanmint.policy \\\n"
                    "    /usr/share/polkit-1/actions/"
                )
            result_msg.exec()
        else:
            if not is_update:
                settings.set("polkit_setup_declined", True)

    def _toggle_theme(self):
        if Theme.is_dark():
            Theme.set_light()
            settings.set("dark_mode", False)
            self.sidebar.theme_button.setText("☾  Dark Mode")
        else:
            Theme.set_dark()
            settings.set("dark_mode", True)
            self.sidebar.theme_button.setText("☀  Light Mode")

        self.setStyleSheet(Theme.stylesheet())
