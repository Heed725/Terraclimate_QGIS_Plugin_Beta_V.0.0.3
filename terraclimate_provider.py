# -*- coding: utf-8 -*-
"""
TerraClimate Provider - Processing provider and plugin management
Version 0.0.3
"""
import importlib
import os
import platform
import subprocess
import sys

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from qgis.core import Qgis, QgsApplication, QgsProcessingProvider

PLUGIN_PROVIDER_ID = "terraclimate_downloader"
PLUGIN_VERSION = "0.0.3"

REQUIRED_PACKAGES = {
    "numpy": "numpy",
    "xarray": "xarray",
    "rioxarray": "rioxarray",
    "netCDF4": "netCDF4",
}

OPTIONAL_PACKAGES = {
    "dask": "dask",
}

MIN_PACKAGE_VERSIONS = {
    "numpy": "1.22",
    "xarray": "2023.1.0",
    "rioxarray": "0.15.0",
    "netCDF4": "1.6.0",
}


def check_package(module_name):
    """Check if a Python package is available."""
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def _parse_version(version_text):
    """Parse a version string into a tuple of integers for lightweight comparison."""
    parts = []
    for chunk in str(version_text).replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def get_package_version(module_name):
    """Return the package version if available."""
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None

    return getattr(module, "__version__", None)


def version_is_compatible(module_name):
    """Check whether an installed module satisfies the minimum tested version."""
    installed = get_package_version(module_name)
    minimum = MIN_PACKAGE_VERSIONS.get(module_name)
    if not installed or not minimum:
        return True
    return _parse_version(installed) >= _parse_version(minimum)


def get_missing_packages():
    """Get lists of missing required and optional packages."""
    missing_required = []
    missing_optional = []

    for module, pip_name in REQUIRED_PACKAGES.items():
        if not check_package(module):
            missing_required.append((module, pip_name))

    for module, pip_name in OPTIONAL_PACKAGES.items():
        if not check_package(module):
            missing_optional.append((module, pip_name))

    return missing_required, missing_optional


def get_incompatible_packages():
    """Return required packages that are installed but older than tested versions."""
    incompatible = []
    for module, pip_name in REQUIRED_PACKAGES.items():
        if check_package(module) and not version_is_compatible(module):
            incompatible.append((module, pip_name, get_package_version(module), MIN_PACKAGE_VERSIONS[module]))
    return incompatible


def dependencies_ready():
    """True when all required packages are installed and meet minimum tested versions."""
    missing_required, _ = get_missing_packages()
    return not missing_required and not get_incompatible_packages()


def get_manual_install_command(include_optional=False):
    """Build a pip command that uses the same Python executable as QGIS."""
    package_names = list(REQUIRED_PACKAGES.values())
    if include_optional:
        package_names.extend(OPTIONAL_PACKAGES.values())
    return f'"{sys.executable}" -m pip install --user ' + " ".join(package_names)


def get_environment_summary():
    """Collect useful environment details for dependency troubleshooting."""
    return [
        f"QGIS Python executable: {sys.executable}",
        f"Python version: {sys.version.split()[0]}",
        f"Platform: {platform.system()} {platform.release()}",
    ]


class DependencyInstallerDialog(QDialog):
    """Dialog for installing Python dependencies."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TerraClimate - Install Dependencies")
        self.setMinimumWidth(680)
        self.setMinimumHeight(420)
        self.setup_ui()
        self.check_status()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel("<h2>TerraClimate Downloader - Dependency Setup</h2>")
        layout.addWidget(header)

        self.status_label = QLabel("Checking dependencies...")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        layout.addWidget(self.log_output)

        btn_layout = QHBoxLayout()

        self.install_btn = QPushButton("Install Required Packages")
        self.install_btn.clicked.connect(self.install_packages)
        btn_layout.addWidget(self.install_btn)

        self.install_all_btn = QPushButton("Install All (Including Optional)")
        self.install_all_btn.clicked.connect(lambda: self.install_packages(include_optional=True))
        btn_layout.addWidget(self.install_all_btn)

        self.refresh_btn = QPushButton("Refresh Status")
        self.refresh_btn.clicked.connect(self.check_status)
        btn_layout.addWidget(self.refresh_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

        help_text = QLabel(
            "<small><b>If automatic installation fails:</b> run this command with the same Python executable QGIS uses:<br>"
            f"<code>{get_manual_install_command(include_optional=False)}</code><br>"
            "Restart QGIS after installation.</small>"
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

    def log(self, message):
        """Add message to log output."""
        self.log_output.append(message)
        QApplication.processEvents()

    def check_status(self):
        """Check and display dependency status."""
        self.log_output.clear()
        missing_req, missing_opt = get_missing_packages()
        incompatible = get_incompatible_packages()
        all_required_ok = not missing_req and not incompatible

        self.log("=" * 60)
        self.log("DEPENDENCY STATUS CHECK")
        self.log("=" * 60)
        self.log("")
        for line in get_environment_summary():
            self.log(line)
        self.log("")

        self.log("REQUIRED PACKAGES:")
        for module in REQUIRED_PACKAGES:
            if check_package(module):
                version_text = get_package_version(module) or "unknown"
                if version_is_compatible(module):
                    self.log(f"  OK {module} - installed ({version_text})")
                else:
                    self.log(
                        f"  UPDATE {module} - installed ({version_text}), "
                        f"tested minimum is {MIN_PACKAGE_VERSIONS[module]}"
                    )
            else:
                self.log(f"  MISSING {module} - not installed")

        self.log("")
        self.log("OPTIONAL PACKAGES:")
        for module in OPTIONAL_PACKAGES:
            if check_package(module):
                version_text = get_package_version(module) or "unknown"
                self.log(f"  OK {module} - installed ({version_text})")
            else:
                self.log(f"  OPTIONAL {module} - not installed")

        if missing_req or incompatible:
            self.log("")
            self.log("MANUAL INSTALL COMMAND:")
            self.log(get_manual_install_command(include_optional=False))

        self.log("")
        self.log("=" * 60)

        if all_required_ok:
            self.status_label.setText(
                "<span style='color: green; font-weight: bold;'>All required dependencies are available.</span>"
            )
            self.status_label.setStyleSheet("padding: 10px; background-color: #d4edda; border-radius: 5px;")
            self.install_btn.setEnabled(False)
            self.log("Ready to use.")
        else:
            self.status_label.setText(
                "<span style='color: red; font-weight: bold;'>Required dependencies are missing or outdated.</span>"
            )
            self.status_label.setStyleSheet("padding: 10px; background-color: #f8d7da; border-radius: 5px;")
            self.install_btn.setEnabled(True)

    def get_pip_executable(self):
        """Find the correct pip executable for QGIS Python."""
        return [sys.executable, "-m", "pip"]

    def install_packages(self, include_optional=False):
        """Install missing packages using pip."""
        missing_req, missing_opt = get_missing_packages()
        incompatible = get_incompatible_packages()

        packages_to_install = [pip_name for _, pip_name in missing_req]
        packages_to_install.extend([pip_name for _, pip_name, _, _ in incompatible])
        if include_optional:
            packages_to_install.extend([pip_name for _, pip_name in missing_opt])

        packages_to_install = list(dict.fromkeys(packages_to_install))
        if not packages_to_install:
            self.log("\nNo packages need to be installed or updated.")
            return

        self.log("")
        self.log("=" * 60)
        self.log("INSTALLING PACKAGES")
        self.log("=" * 60)
        self.log(f"Command base: {' '.join(self.get_pip_executable())}")
        self.log(f"Packages: {', '.join(packages_to_install)}")
        self.log("")

        self.install_btn.setEnabled(False)
        self.install_all_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)

        self.progress.setVisible(True)
        self.progress.setRange(0, len(packages_to_install))
        self.progress.setValue(0)

        pip_cmd = self.get_pip_executable()
        success_count = 0

        for index, package in enumerate(packages_to_install, start=1):
            self.log(f"Installing {package}...")
            QApplication.processEvents()

            try:
                cmd = pip_cmd + ["install", "--user", "--upgrade", package]
                self.log(f"  Command: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                if result.returncode == 0:
                    self.log(f"  OK {package} installed or updated.")
                    success_count += 1
                else:
                    self.log(f"  FAILED {package}")
                    if result.stderr:
                        self.log(f"  Error: {result.stderr[:500]}")
                    self.log("  This QGIS Python environment may not allow plugin-managed installs.")
                    self.log(f"  Manual command: {get_manual_install_command(include_optional=include_optional)}")
            except subprocess.TimeoutExpired:
                self.log(f"  FAILED {package} timed out.")
            except Exception as exc:
                self.log(f"  FAILED {package}: {exc}")

            self.progress.setValue(index)
            QApplication.processEvents()

        self.log("")
        self.log("=" * 60)
        self.log(f"Installation complete: {success_count}/{len(packages_to_install)} packages")
        self.log("=" * 60)

        if success_count == len(packages_to_install):
            self.log("\nAll packages installed or updated. Restart QGIS for changes to take effect.")
            QMessageBox.information(
                self,
                "Installation Complete",
                "All packages were installed successfully.\n\nPlease restart QGIS for the changes to take effect.",
            )
        else:
            self.log("\nSome packages could not be installed automatically.")
            self.log("Use this command in the same Python environment as QGIS:")
            self.log(get_manual_install_command(include_optional=include_optional))

        self.install_btn.setEnabled(True)
        self.install_all_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.check_status()


class TerraClimateProvider(QgsProcessingProvider):
    """Processing provider for TerraClimate algorithms."""

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))

    def loadAlgorithms(self):
        from .terraclimate_algorithm import TerraClimateClipByYear_GDAL
        from .split_raster_bands_algorithm import SplitRasterBands

        self.addAlgorithm(TerraClimateClipByYear_GDAL())
        self.addAlgorithm(SplitRasterBands())

    def id(self):
        return PLUGIN_PROVIDER_ID

    def name(self):
        return self.tr("TerraClimate Downloader")

    def longName(self):
        return self.name()

    def tr(self, text):
        return QCoreApplication.translate("TerraClimateProvider", text)


class TerraClimateProviderPlugin:
    """Main plugin class - registers provider and manages menu entries."""

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.menu_name = "TerraClimate Downloader"
        self.toolbar = None
        self.icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")

    def initGui(self):
        """Initialize the plugin GUI."""
        self.toolbar = self.iface.addToolBar(self.menu_name)
        self.toolbar.setObjectName("TerraClimateToolbar")

        deps_ok = dependencies_ready()

        self.provider = TerraClimateProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        icon = QIcon(self.icon_path) if os.path.exists(self.icon_path) else QIcon()
        action_text = "Open TerraClimate Downloader" if deps_ok else "Open TerraClimate Downloader (Setup Required)"
        action_tooltip = (
            "Download and clip TerraClimate climate data"
            if deps_ok else
            "Open dependency diagnostics and installation help"
        )

        self.action_open = QAction(icon, action_text, self.iface.mainWindow())
        self.action_open.setToolTip(action_tooltip)
        self.action_open.triggered.connect(self.open_tool_dialog)
        self.iface.addPluginToMenu(self.menu_name, self.action_open)
        self.toolbar.addAction(self.action_open)
        self.actions.append(self.action_open)

        self.action_install = QAction(
            QIcon.fromTheme("system-software-install"),
            "Install Dependencies...",
            self.iface.mainWindow(),
        )
        self.action_install.setToolTip("Install or update Python packages for TerraClimate Downloader")
        self.action_install.triggered.connect(self.show_installer_dialog)
        self.iface.addPluginToMenu(self.menu_name, self.action_install)
        self.actions.append(self.action_install)

        self.action_help = QAction(
            QIcon.fromTheme("help-about"),
            "About / Help",
            self.iface.mainWindow(),
        )
        self.action_help.triggered.connect(self.show_help)
        self.iface.addPluginToMenu(self.menu_name, self.action_help)
        self.actions.append(self.action_help)

        if not deps_ok:
            self.iface.messageBar().pushMessage(
                "TerraClimate Downloader",
                "Dependencies need attention. Open Plugins > TerraClimate Downloader > Install Dependencies.",
                level=Qgis.Warning,
                duration=10,
            )

    def unload(self):
        """Unload the plugin."""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu_name, action)
            if self.toolbar:
                self.toolbar.removeAction(action)
        self.actions = []

        if self.toolbar:
            self.toolbar.deleteLater()
            self.toolbar = None

        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def open_tool_dialog(self):
        """Open the main processing tool dialog."""
        missing_req, _ = get_missing_packages()
        incompatible = get_incompatible_packages()

        if missing_req or incompatible:
            details = []
            if missing_req:
                details.append("Missing packages: " + ", ".join([module for module, _ in missing_req]))
            if incompatible:
                details.append(
                    "Outdated packages: " +
                    ", ".join([f"{module} ({installed} < {minimum})" for module, _, installed, minimum in incompatible])
                )
            details.append(f"QGIS Python: {sys.executable}")

            reply = QMessageBox.question(
                self.iface.mainWindow(),
                "Dependencies Required",
                "TerraClimate Downloader needs Python dependencies before it can run.\n\n"
                + "\n".join(details)
                + "\n\nWould you like to open the dependency installer?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self.show_installer_dialog()
            return

        try:
            import processing

            alg_id = f"{PLUGIN_PROVIDER_ID}:terraclimate_clip_remote_to_layer_gdalclip"
            processing.execAlgorithmDialog(alg_id, {})
        except Exception as exc:
            self.iface.messageBar().pushMessage(
                "TerraClimate Downloader",
                f"Could not open dialog: {exc}. Try the Processing Toolbox instead.",
                level=Qgis.Warning,
                duration=5,
            )

    def show_installer_dialog(self):
        """Show the dependency installer dialog."""
        dialog = DependencyInstallerDialog(self.iface.mainWindow())
        dialog.exec_()

        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = TerraClimateProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

        deps_ok = dependencies_ready()
        if hasattr(self, "action_open"):
            self.action_open.setText(
                "Open TerraClimate Downloader" if deps_ok else "Open TerraClimate Downloader (Setup Required)"
            )

    def show_help(self):
        """Show help/about dialog."""
        help_text = f"""
        <h2>TerraClimate Downloader v{PLUGIN_VERSION}</h2>
        <p><b>Author:</b> Hemed Lungo</p>
        <p><b>Email:</b> Hemedlungo@gmail.com</p>

        <h3>Description</h3>
        <p>Download and clip TerraClimate climate data for any region on Earth.</p>
        <p>Supports single-year downloads and multi-year stacks through 2025.</p>

        <h3>Dependencies</h3>
        <p>The tool uses the QGIS Python environment plus these packages:</p>
        <ul>
            <li>Required: numpy, xarray, rioxarray, netCDF4</li>
            <li>Optional: dask</li>
        </ul>
        <p>If the tool does not open, use <b>Plugins &gt; TerraClimate Downloader &gt; Install Dependencies</b>.</p>

        <h3>Manual Install Command</h3>
        <p><code>{get_manual_install_command(include_optional=False)}</code></p>

        <h3>Links</h3>
        <p>
            <a href="https://github.com/Heed725/Terraclimate_QGIS_Plugin/">GitHub Repository</a><br>
            <a href="https://www.climatologylab.org/terraclimate.html">TerraClimate Dataset</a>
        </p>
        """

        QMessageBox.about(
            self.iface.mainWindow(),
            "About TerraClimate Downloader",
            help_text,
        )
