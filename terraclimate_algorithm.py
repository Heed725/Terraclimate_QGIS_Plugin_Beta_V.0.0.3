# -*- coding: utf-8 -*-
"""
TerraClimate Algorithm - Processing algorithm for downloading and clipping TerraClimate data
Version 0.0.3
"""
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFileDestination,
    QgsProcessingException,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsGeometry,
    QgsVectorLayer,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsRasterBandStats
)
import os
import time
import tempfile

from .terraclimate_provider import get_incompatible_packages, get_manual_install_command


class TerraClimateClipByYear_GDAL(QgsProcessingAlgorithm):
    """
    Processing algorithm to download and clip TerraClimate data.
    
    Downloads a bounding box subset from TerraClimate OPeNDAP server using xarray,
    then clips to the exact boundary using GDAL.
    """
    
    # Parameter names
    INPUT_VECTOR = 'INPUT_VECTOR'
    VARIABLE = 'VARIABLE'
    YEAR_MODE = 'YEAR_MODE'
    YEAR = 'YEAR'
    END_YEAR = 'END_YEAR'
    TIME_INDEX = 'TIME_INDEX'
    BUFFER_DEG = 'BUFFER_DEG'
    MAX_RETRIES = 'MAX_RETRIES'
    OUTPUT_TIF = 'OUTPUT_TIF'
    
    # Available variables with descriptions
    VAR_OPTIONS = [
        'aet', 'def', 'pdsi', 'pet', 'ppt', 'q', 
        'soil', 'srad', 'swe', 'tmax', 'tmin', 'vap', 'vpd', 'ws'
    ]
    VAR_OPTIONS_LABELS = [
        'Actual Evapotranspiration (aet) - mm',
        'Climatic Water Deficit (def) - mm',
        'Palmer Drought Severity Index (pdsi)',
        'Potential Evapotranspiration (pet) - mm',
        'Precipitation (ppt) - mm',
        'Runoff (q) - mm',
        'Soil Moisture (soil) - mm',
        'Downward Shortwave Radiation (srad) - W/m²',
        'Snow Water Equivalent (swe) - mm',
        'Maximum Temperature (tmax) - °C',
        'Minimum Temperature (tmin) - °C',
        'Vapor Pressure (vap) - kPa',
        'Vapor Pressure Deficit (vpd) - kPa',
        'Wind Speed (ws) - m/s'
    ]

    YEAR_MODE_OPTIONS = [
        'Single year',
        'Year range'
    ]
    
    # TerraClimate OPeNDAP base URL
    BASE_URL = 'http://thredds.northwestknowledge.net:8080/thredds/dodsC/TERRACLIMATE_ALL/data'
    
    # Year range (extended to 2025)
    MIN_YEAR = 1958
    MAX_YEAR = 2025
    
    # Color ramp configurations for different variable types
    # Format: 'variable': ('ramp_type', 'description')
    # ramp_types: 'temperature', 'precipitation', 'moisture', 'radiation', 'wind', 'drought'
    VAR_COLOR_RAMPS = {
        'aet': 'moisture',      # Actual Evapotranspiration
        'def': 'deficit',       # Climatic Water Deficit
        'pdsi': 'drought',      # Palmer Drought Severity Index
        'pet': 'moisture',      # Potential Evapotranspiration
        'ppt': 'precipitation', # Precipitation
        'q': 'precipitation',   # Runoff
        'soil': 'moisture',     # Soil Moisture
        'srad': 'radiation',    # Downward Shortwave Radiation
        'swe': 'snow',          # Snow Water Equivalent
        'tmax': 'temperature',  # Maximum Temperature
        'tmin': 'temperature',  # Minimum Temperature
        'vap': 'moisture',      # Vapor Pressure
        'vpd': 'deficit',       # Vapor Pressure Deficit
        'ws': 'wind',           # Wind Speed
    }

    def createInstance(self):
        return TerraClimateClipByYear_GDAL()

    def tr(self, text):
        return QCoreApplication.translate('TerraClimateClipByYear_GDAL', text)

    def name(self):
        return 'terraclimate_clip_remote_to_layer_gdalclip'

    def displayName(self):
        return self.tr('Download TerraClimate Data')

    def group(self):
        return self.tr('TerraClimate Downloader')

    def groupId(self):
        return 'terraclimatedownloader'
    
    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))

    def shortHelpString(self):
        return self.tr(
            '<h3>Download TerraClimate Data</h3>'
            '<p>Downloads climate data from TerraClimate and clips it to your area of interest.</p>'
            
            '<h4>Parameters</h4>'
            '<ul>'
            '<li><b>Input polygon layer:</b> Your area of interest (boundary)</li>'
            '<li><b>Variable:</b> Climate variable to download (temperature, precipitation, etc.)</li>'
            '<li><b>Year:</b> Single year or start year (1958-2025)</li>'
            '<li><b>End year:</b> Used in year range mode to build a multi-year stack</li>'
            '<li><b>Time index:</b> -1 for all months, or 1-12 for a specific month in single-year mode</li>'
            '<li><b>Buffer:</b> Extra area around your boundary (in degrees)</li>'
            '<li><b>Output:</b> Where to save the GeoTIFF file</li>'
            '</ul>'
            
            '<h4>How it works</h4>'
            '<ol>'
            '<li>Connects to TerraClimate OPeNDAP server</li>'
            '<li>Downloads only the data for your bounding box (efficient)</li>'
            '<li>Clips the data precisely to your boundary polygon</li>'
            '<li>Saves one GeoTIFF and adds it to your map</li>'
            '</ol>'
            
            '<h4>Credits</h4>'
            '<p>TerraClimate dataset by Abatzoglou, J.T., S.Z. Dobrowski, '
            'S.A. Parks, K.C. Hegewisch (2018), Scientific Data.</p>'
            '<p>Icon by Fusion5085</p>'
        )

    def initAlgorithm(self, config=None):
        # Input polygon layer
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_VECTOR,
            self.tr('Input polygon layer (area of interest)'),
            [QgsProcessing.TypeVectorPolygon]
        ))
        
        # Variable selection
        self.addParameter(QgsProcessingParameterEnum(
            self.VARIABLE,
            self.tr('Climate variable'),
            options=self.VAR_OPTIONS_LABELS,
            defaultValue=self.VAR_OPTIONS.index('tmax')
        ))

        self.addParameter(QgsProcessingParameterEnum(
            self.YEAR_MODE,
            self.tr('Year selection mode'),
            options=self.YEAR_MODE_OPTIONS,
            defaultValue=0
        ))
        
        # Year selection
        self.addParameter(QgsProcessingParameterNumber(
            self.YEAR,
            self.tr(f'Year ({self.MIN_YEAR}-{self.MAX_YEAR})'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=2024,
            minValue=self.MIN_YEAR,
            maxValue=self.MAX_YEAR
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.END_YEAR,
            self.tr(f'End year ({self.MIN_YEAR}-{self.MAX_YEAR}, used for year range mode)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=2025,
            minValue=self.MIN_YEAR,
            maxValue=self.MAX_YEAR
        ))
        
        # Time index (month selection)
        self.addParameter(QgsProcessingParameterNumber(
            self.TIME_INDEX,
            self.tr('Month (-1 = all months as bands, 1-12 = specific month; ignored in year range mode)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=-1,
            minValue=-1,
            maxValue=12
        ))
        
        # Buffer
        self.addParameter(QgsProcessingParameterNumber(
            self.BUFFER_DEG,
            self.tr('Bounding box buffer (degrees)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.1,
            minValue=0.0,
            maxValue=5.0
        ))
        
        # Retries
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_RETRIES,
            self.tr('Connection retries'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=3,
            minValue=1,
            maxValue=10
        ))
        
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_TIF,
            self.tr('Output GeoTIFF'),
            'GeoTIFF (*.tif *.tiff)'
        ))

    def _check_dependencies(self):
        """Check if required dependencies are available."""
        missing = []
        outdated = []
        try:
            import xarray
        except ImportError:
            missing.append('xarray')
        try:
            import rioxarray
        except ImportError:
            missing.append('rioxarray')
        try:
            import numpy
        except ImportError:
            missing.append('numpy')
        try:
            import netCDF4
        except ImportError:
            missing.append('netCDF4')

        for module_name, _, installed, minimum in get_incompatible_packages():
            outdated.append(f"{module_name} ({installed} < {minimum})")

        return missing, outdated

    def _ensure_layer(self, maybe_layer, name_hint='mask'):
        """Ensure we have a valid QgsVectorLayer object."""
        if hasattr(maybe_layer, 'getFeatures'):
            return maybe_layer
        if isinstance(maybe_layer, str):
            lyr = QgsVectorLayer(maybe_layer, name_hint, 'ogr')
            return lyr if lyr and lyr.isValid() else None
        return None

    def _get_color_ramp_items(self, ramp_type, min_val, max_val):
        """
        Get color ramp items for a given variable type.
        Returns a list of QgsColorRampShader.ColorRampItem objects.
        
        Args:
            ramp_type: Type of color ramp ('temperature', 'precipitation', etc.)
            min_val: Minimum data value
            max_val: Maximum data value
        
        Returns:
            List of QgsColorRampShader.ColorRampItem
        """
        items = []
        
        # Calculate range for value positions
        val_range = max_val - min_val
        
        def make_item(fraction, color):
            """Helper to create color ramp item at a fraction of the range."""
            value = min_val + val_range * fraction
            return QgsColorRampShader.ColorRampItem(value, color, f'{value:.1f}')
        
        if ramp_type == 'temperature':
            # Blue (cold) -> White -> Red (hot)
            items = [
                make_item(0.0, QColor(49, 54, 149)),      # Dark blue
                make_item(0.25, QColor(116, 173, 209)),   # Light blue
                make_item(0.5, QColor(255, 255, 191)),    # Yellow/white
                make_item(0.75, QColor(244, 109, 67)),    # Orange
                make_item(1.0, QColor(165, 0, 38)),       # Dark red
            ]
        
        elif ramp_type == 'precipitation':
            # Light -> Dark blue (more rain = darker)
            items = [
                make_item(0.0, QColor(255, 255, 204)),    # Light yellow
                make_item(0.25, QColor(161, 218, 180)),   # Light green
                make_item(0.5, QColor(65, 182, 196)),     # Cyan
                make_item(0.75, QColor(44, 127, 184)),    # Blue
                make_item(1.0, QColor(37, 52, 148)),      # Dark blue
            ]
        
        elif ramp_type == 'moisture':
            # Brown (dry) -> Green -> Blue (wet)
            items = [
                make_item(0.0, QColor(166, 97, 26)),      # Brown
                make_item(0.25, QColor(223, 194, 125)),   # Tan
                make_item(0.5, QColor(128, 205, 193)),    # Teal
                make_item(0.75, QColor(53, 151, 143)),    # Green-blue
                make_item(1.0, QColor(1, 102, 94)),       # Dark teal
            ]
        
        elif ramp_type == 'deficit':
            # Green (low deficit) -> Yellow -> Red (high deficit)
            items = [
                make_item(0.0, QColor(0, 104, 55)),       # Dark green
                make_item(0.25, QColor(166, 217, 106)),   # Light green
                make_item(0.5, QColor(255, 255, 191)),    # Yellow
                make_item(0.75, QColor(253, 174, 97)),    # Orange
                make_item(1.0, QColor(165, 0, 38)),       # Dark red
            ]
        
        elif ramp_type == 'drought':
            # PDSI: Red (dry/negative) -> White (normal/zero) -> Blue (wet/positive)
            # PDSI typically ranges from about -10 to +10, with 0 being normal
            # We need to handle the actual data range but try to center on zero if possible
            
            # Check if zero is within the range
            if min_val < 0 < max_val:
                # Zero is in range - center the white on zero
                # Calculate where zero falls in the 0-1 range
                zero_pos = -min_val / val_range
                
                items = [
                    make_item(0.0, QColor(165, 0, 38)),       # Dark red (extreme drought)
                    make_item(zero_pos * 0.5, QColor(244, 109, 67)),  # Orange
                    make_item(zero_pos, QColor(255, 255, 191)),       # Yellow/white at zero
                    make_item(zero_pos + (1 - zero_pos) * 0.5, QColor(116, 173, 209)),  # Light blue
                    make_item(1.0, QColor(49, 54, 149)),      # Dark blue (wet)
                ]
            else:
                # Zero not in range - use regular distribution
                items = [
                    make_item(0.0, QColor(165, 0, 38)),       # Dark red
                    make_item(0.25, QColor(244, 109, 67)),    # Orange
                    make_item(0.5, QColor(255, 255, 191)),    # Yellow/white
                    make_item(0.75, QColor(116, 173, 209)),   # Light blue
                    make_item(1.0, QColor(49, 54, 149)),      # Dark blue
                ]
        
        elif ramp_type == 'radiation':
            # Purple -> Yellow -> White (more radiation = brighter)
            items = [
                make_item(0.0, QColor(63, 0, 125)),       # Dark purple
                make_item(0.25, QColor(136, 65, 157)),    # Purple
                make_item(0.5, QColor(247, 182, 67)),     # Orange-yellow
                make_item(0.75, QColor(255, 237, 160)),   # Light yellow
                make_item(1.0, QColor(255, 255, 229)),    # Near white
            ]
        
        elif ramp_type == 'snow':
            # Light blue -> Dark blue (more snow = darker)
            items = [
                make_item(0.0, QColor(240, 249, 255)),    # Very light blue
                make_item(0.25, QColor(189, 215, 231)),   # Light blue
                make_item(0.5, QColor(107, 174, 214)),    # Medium blue
                make_item(0.75, QColor(49, 130, 189)),    # Blue
                make_item(1.0, QColor(8, 81, 156)),       # Dark blue
            ]
        
        elif ramp_type == 'wind':
            # Light -> Dark green (calm to windy)
            items = [
                make_item(0.0, QColor(255, 255, 204)),    # Light yellow
                make_item(0.25, QColor(194, 230, 153)),   # Light green
                make_item(0.5, QColor(120, 198, 121)),    # Green
                make_item(0.75, QColor(49, 163, 84)),     # Dark green
                make_item(1.0, QColor(0, 104, 55)),       # Very dark green
            ]
        
        else:
            # Default: Viridis-like
            items = [
                make_item(0.0, QColor(68, 1, 84)),        # Dark purple
                make_item(0.25, QColor(59, 82, 139)),     # Blue-purple
                make_item(0.5, QColor(33, 145, 140)),     # Teal
                make_item(0.75, QColor(94, 201, 98)),     # Green
                make_item(1.0, QColor(253, 231, 37)),     # Yellow
            ]
        
        return items

    def _apply_pseudocolor_style(self, rlayer, var_name, band=1, feedback=None):
        """
        Apply singleband pseudocolor styling to a raster layer.
        
        For multiband rasters (12 months), calculates min/max across ALL bands
        to ensure consistent color scaling when switching between months.
        
        Args:
            rlayer: QgsRasterLayer to style
            var_name: Variable name to determine appropriate color ramp
            band: Band number to style (default 1)
            feedback: Optional feedback object for logging
        """
        try:
            provider = rlayer.dataProvider()
            band_count = rlayer.bandCount()
            
            # For multiband rasters, calculate global min/max across all bands
            # This ensures consistent coloring when user switches between bands/months
            if band_count > 1:
                if feedback:
                    feedback.pushInfo(f"  Calculating min/max across all {band_count} bands...")
                
                global_min = float('inf')
                global_max = float('-inf')
                
                for b in range(1, band_count + 1):
                    stats = provider.bandStatistics(
                        b, 
                        QgsRasterBandStats.Min | QgsRasterBandStats.Max,
                        rlayer.extent(),
                        0  # Sample size 0 = use all pixels
                    )
                    
                    # Skip NoData values - bandStatistics should already exclude them
                    # but we also check for obviously invalid values
                    if stats.minimumValue != -9999.0 and stats.minimumValue < global_min:
                        global_min = stats.minimumValue
                    if stats.maximumValue != -9999.0 and stats.maximumValue > global_max:
                        global_max = stats.maximumValue
                
                min_val = global_min
                max_val = global_max
                
                if feedback:
                    feedback.pushInfo(f"  Global stats across all bands: min={min_val:.2f}, max={max_val:.2f}")
            else:
                # Single band - just get stats for that band
                stats = provider.bandStatistics(
                    band, 
                    QgsRasterBandStats.Min | QgsRasterBandStats.Max,
                    rlayer.extent(),
                    0
                )
                min_val = stats.minimumValue
                max_val = stats.maximumValue
                
                if feedback:
                    feedback.pushInfo(f"  Band {band} stats: min={min_val:.2f}, max={max_val:.2f}")
            
            # Handle edge cases
            if min_val == float('inf') or max_val == float('-inf'):
                if feedback:
                    feedback.reportError("  Warning: Could not calculate valid statistics")
                return False
            
            # Handle case where min equals max (constant raster)
            if min_val == max_val:
                if feedback:
                    feedback.pushInfo(f"  Note: Constant value raster ({min_val:.2f})")
                # Add small buffer to avoid division issues
                max_val = min_val + 1
            
            # Get appropriate color ramp type
            ramp_type = self.VAR_COLOR_RAMPS.get(var_name, 'default')
            
            # Create color ramp shader
            shader = QgsRasterShader()
            color_ramp = QgsColorRampShader()
            color_ramp.setColorRampType(QgsColorRampShader.Interpolated)
            
            # For classification, we can also set min/max explicitly
            color_ramp.setMinimumValue(min_val)
            color_ramp.setMaximumValue(max_val)
            
            # Get color ramp items based on actual data range
            items = self._get_color_ramp_items(ramp_type, min_val, max_val)
            color_ramp.setColorRampItemList(items)
            
            # Classify method - Continuous for smooth gradients
            color_ramp.setClassificationMode(QgsColorRampShader.Continuous)
            
            shader.setRasterShaderFunction(color_ramp)
            
            # Create and apply renderer
            renderer = QgsSingleBandPseudoColorRenderer(provider, band, shader)
            
            # Set the min/max on the renderer as well
            renderer.setClassificationMin(min_val)
            renderer.setClassificationMax(max_val)
            
            rlayer.setRenderer(renderer)
            
            # Trigger repaint
            rlayer.triggerRepaint()
            
            if feedback:
                feedback.pushInfo(f"  Applied {ramp_type} color ramp (range: {min_val:.2f} to {max_val:.2f})")
            
            return True
            
        except Exception as e:
            if feedback:
                feedback.reportError(f"  Warning: Could not apply styling: {str(e)}")
            return False

    def _open_dataset_with_retry(self, xr, url, max_retries, feedback):
        """Open a remote dataset with retries."""
        last_err = None
        for attempt in range(1, max_retries + 1):
            if feedback.isCanceled():
                raise QgsProcessingException('Cancelled by user.')

            try:
                feedback.pushInfo(f"  Attempt {attempt}/{max_retries}: {url}")
                ds = xr.open_dataset(url)
                feedback.pushInfo("  Connected successfully.")
                return ds
            except Exception as exc:
                last_err = exc
                feedback.pushInfo(f"  Failed: {str(exc)[:120]}")
                if attempt < max_retries:
                    feedback.pushInfo("  Waiting 5 seconds before retry...")
                    time.sleep(5)

        raise QgsProcessingException(
            f'Could not connect to TerraClimate server after {max_retries} attempts.\n'
            f'Last error: {last_err}\n\n'
            'Please check your internet connection and try again.'
        )

    def _sanitize_cf(self, arr):
        """Remove conflicting CF metadata before writing a raster."""
        arr = arr.copy()
        for key in ['_FillValue', 'missing_value', 'scale_factor', 'add_offset']:
            arr.attrs.pop(key, None)
        if hasattr(arr, 'encoding') and isinstance(arr.encoding, dict):
            for key in ['_FillValue', 'missing_value', 'scale_factor', 'add_offset']:
                arr.encoding.pop(key, None)
        return arr

    def _prepare_subset(self, da, minx, miny, maxx, maxy, time_index, feedback):
        """Subset a TerraClimate variable to the AOI bounding box and optional month."""
        lon_name = 'lon' if 'lon' in da.dims else ('longitude' if 'longitude' in da.dims else None)
        lat_name = 'lat' if 'lat' in da.dims else ('latitude' if 'latitude' in da.dims else None)

        if lon_name is None or lat_name is None:
            raise QgsProcessingException(f'Could not identify lon/lat dimensions. Found: {da.dims}')

        if 'time' in da.dims and time_index != -1:
            idx0 = time_index - 1
            if idx0 < 0 or idx0 >= da.sizes['time']:
                raise QgsProcessingException(
                    f'Month {time_index} out of range. Dataset has {da.sizes["time"]} time steps.'
                )
            da = da.isel(time=idx0)
            feedback.pushInfo(f"  Selected month {time_index}")

        try:
            da = da.rio.write_crs("EPSG:4326", inplace=False)
        except Exception:
            pass
        da = da.rio.set_spatial_dims(x_dim=lon_name, y_dim=lat_name, inplace=False)

        lon_vals = da[lon_name].values
        lat_vals = da[lat_name].values
        lon_inc = lon_vals[0] < lon_vals[-1]
        lat_inc = lat_vals[0] < lat_vals[-1]

        lon_slice = slice(minx, maxx) if lon_inc else slice(maxx, minx)
        lat_slice = slice(miny, maxy) if lat_inc else slice(maxy, miny)

        sub = da.sel({lon_name: lon_slice, lat_name: lat_slice})

        if not lon_inc:
            sub = sub.sortby(lon_name)
        if not lat_inc:
            sub = sub.sortby(lat_name)

        try:
            sub = sub.rio.write_crs("EPSG:4326", inplace=False)
        except Exception:
            pass
        sub = sub.rio.set_spatial_dims(x_dim=lon_name, y_dim=lat_name, inplace=False)
        return sub

    def _prepare_for_raster_export(self, arr):
        """Normalize dimensions so rioxarray writes multi-time data as raster bands."""
        x_dim = arr.rio.x_dim
        y_dim = arr.rio.y_dim

        if 'time' in arr.dims:
            arr = arr.rename({'time': 'band'})
            arr = arr.assign_coords(band=list(range(1, arr.sizes['band'] + 1)))
            arr = arr.transpose('band', y_dim, x_dim)
        else:
            arr = arr.transpose(y_dim, x_dim)

        return arr

    def processAlgorithm(self, parameters, context, feedback):
        # Check dependencies first
        missing, outdated = self._check_dependencies()
        if missing or outdated:
            raise QgsProcessingException(
                (
                    (f"Missing required Python packages: {', '.join(missing)}\n" if missing else "")
                    + (f"Outdated packages: {', '.join(outdated)}\n" if outdated else "")
                    + "\nPlease install or update them using:\n"
                    + "  Plugins > TerraClimate Downloader > Install Dependencies\n\n"
                    + "Or manually with the same Python that QGIS uses:\n"
                    + f"  {get_manual_install_command(include_optional=False)}"
                )
            )
        
        # Now import the dependencies
        import xarray as xr
        import rioxarray
        import numpy as np
        import processing
        
        # Get parameters
        layer = self.parameterAsVectorLayer(parameters, self.INPUT_VECTOR, context)
        if layer is None:
            raise QgsProcessingException('Invalid input vector layer.')
        
        var_idx = self.parameterAsEnum(parameters, self.VARIABLE, context)
        var_name = self.VAR_OPTIONS[var_idx]
        var_label = self.VAR_OPTIONS_LABELS[var_idx]
        
        year_mode = self.parameterAsEnum(parameters, self.YEAR_MODE, context)
        start_year = self.parameterAsInt(parameters, self.YEAR, context)
        end_year = self.parameterAsInt(parameters, self.END_YEAR, context)
        time_index = self.parameterAsInt(parameters, self.TIME_INDEX, context)
        buffer_deg = float(self.parameterAsDouble(parameters, self.BUFFER_DEG, context))
        max_retries = self.parameterAsInt(parameters, self.MAX_RETRIES, context)
        out_tif = self.parameterAsFileOutput(parameters, self.OUTPUT_TIF, context)

        if not (self.MIN_YEAR <= start_year <= self.MAX_YEAR):
            raise QgsProcessingException(f'Year must be between {self.MIN_YEAR} and {self.MAX_YEAR}.')
        if not (self.MIN_YEAR <= end_year <= self.MAX_YEAR):
            raise QgsProcessingException(f'End year must be between {self.MIN_YEAR} and {self.MAX_YEAR}.')
        if end_year < start_year:
            raise QgsProcessingException('End year must be greater than or equal to the start year.')

        if year_mode == 0:
            selected_years = [start_year]
            year_mode_label = "Single year"
        else:
            selected_years = list(range(start_year, end_year + 1))
            year_mode_label = "Year range"
            time_index = -1

        if time_index != -1 and not (1 <= time_index <= 12):
            raise QgsProcessingException('Time index must be -1 (all months) or 1-12 (specific month).')
        
        feedback.pushInfo("=" * 60)
        feedback.pushInfo(f"TerraClimate Downloader v0.0.3")
        feedback.pushInfo("=" * 60)
        feedback.pushInfo(f"Variable: {var_label}")
        feedback.pushInfo(f"Mode: {year_mode_label}")
        if len(selected_years) == 1:
            feedback.pushInfo(f"Year: {selected_years[0]}")
        else:
            feedback.pushInfo(f"Years: {selected_years[0]}-{selected_years[-1]}")
        feedback.pushInfo(f"Month selection: {'All months' if time_index == -1 else time_index}")
        feedback.pushInfo(f"Expected output bands: {len(selected_years) * (12 if time_index == -1 else 1)}")
        feedback.pushInfo(f"Output file: {out_tif}")
        feedback.pushInfo("")
        
        # Get layer extent in WGS84
        feedback.pushInfo("Step 1: Calculating bounding box...")
        crs_src = layer.crs()
        crs_wgs = QgsCoordinateReferenceSystem('EPSG:4326')
        
        if not crs_src.isValid():
            raise QgsProcessingException('Input layer has invalid CRS.')
        
        xform = None
        if crs_src != crs_wgs:
            xform = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())
        
        # Calculate combined extent of all features
        extent_wgs = None
        feature_count = 0
        for f in layer.getFeatures():
            g = f.geometry()
            if not g or g.isEmpty():
                continue
            
            g_wgs = QgsGeometry.fromWkt(g.asWkt())
            if xform and g_wgs.transform(xform) != 0:
                raise QgsProcessingException('Failed to reproject geometry to EPSG:4326.')
            
            # Clean geometry
            try:
                g_wgs = g_wgs.buffer(0, 1)
            except Exception:
                pass
            
            if extent_wgs is None:
                extent_wgs = g_wgs.boundingBox()
            else:
                extent_wgs.combineExtentWith(g_wgs.boundingBox())
            feature_count += 1
        
        if extent_wgs is None:
            raise QgsProcessingException('No valid geometries found in input layer.')
        
        feedback.pushInfo(f"  Found {feature_count} feature(s)")
        
        # Apply buffer
        minx = extent_wgs.xMinimum() - buffer_deg
        miny = extent_wgs.yMinimum() - buffer_deg
        maxx = extent_wgs.xMaximum() + buffer_deg
        maxy = extent_wgs.yMaximum() + buffer_deg
        
        feedback.pushInfo(f"  Bounding box: ({minx:.4f}, {miny:.4f}) to ({maxx:.4f}, {maxy:.4f})")
        feedback.pushInfo("")
        
        # Dissolve layer for clean clipping
        feedback.pushInfo("Step 2: Preparing mask layer...")
        try:
            dis = processing.run(
                "native:dissolve",
                {
                    "INPUT": layer,
                    "FIELD": [],
                    "SEPARATE_DISJOINT": False,
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT
                },
                context=context,
                is_child_algorithm=True
            )
            mask_layer = self._ensure_layer(dis["OUTPUT"], "mask_dissolved")
            if mask_layer is None:
                feedback.reportError('  Warning: Dissolve output could not be loaded, using original layer.')
                mask_layer = layer
            else:
                feedback.pushInfo("  Dissolved features successfully")
        except Exception as e:
            feedback.reportError(f'  Warning: Dissolve failed ({e}), using original layer.')
            mask_layer = layer
        feedback.pushInfo("")
        
        feedback.pushInfo("Step 3: Downloading TerraClimate subset...")
        temp_unclipped = None
        try:
            sub_arrays = []
            for current_year in selected_years:
                url = f'{self.BASE_URL}/TerraClimate_{var_name}_{current_year}.nc'
                feedback.pushInfo(f"  Year {current_year}: opening dataset")
                ds = self._open_dataset_with_retry(xr, url, max_retries, feedback)
                try:
                    if var_name not in ds.variables:
                        available = [v for v in ds.variables if not v.startswith('crs')]
                        raise QgsProcessingException(
                            f'Variable "{var_name}" not found in dataset for {current_year}.\n'
                            f'Available variables: {available}'
                        )

                    year_sub = self._prepare_subset(ds[var_name], minx, miny, maxx, maxy, time_index, feedback)
                    year_sub = self._sanitize_cf(year_sub)
                    mb = float(year_sub.nbytes) / 1e6 if hasattr(year_sub, 'nbytes') else 0.0
                    band_count = year_sub.sizes.get('time', 1) if hasattr(year_sub, 'sizes') else 1
                    feedback.pushInfo(f"    Prepared {band_count} band(s), about {mb:.2f} MB")
                    sub_arrays.append(year_sub.load())
                finally:
                    try:
                        ds.close()
                    except Exception:
                        pass

            if not sub_arrays:
                raise QgsProcessingException('No TerraClimate rasters were prepared for export.')

            if len(sub_arrays) == 1:
                sub = sub_arrays[0]
            else:
                sub = xr.concat(sub_arrays, dim='time')

            sub = self._prepare_for_raster_export(sub)

            try:
                sub = sub.rio.write_nodata(np.float32(-9999.0), inplace=False)
            except Exception:
                pass

            if str(sub.dtype) != 'float32':
                sub = sub.astype('float32')

            feedback.pushInfo("")
            feedback.pushInfo("Step 4: Writing temporary raster...")
            fd, temp_unclipped = tempfile.mkstemp(suffix=".tif")
            os.close(fd)
            sub.rio.to_raster(temp_unclipped)
            feedback.pushInfo(f"  Saved to: {temp_unclipped}")
            feedback.pushInfo("")

            feedback.pushInfo("Step 5: Clipping to boundary...")
            clip_params = {
                'INPUT': temp_unclipped,
                'MASK': mask_layer,
                'SOURCE_CRS': None,
                'TARGET_CRS': None,
                'NODATA': -9999.0,
                'ALPHA_BAND': False,
                'CROP_TO_CUTLINE': True,
                'KEEP_RESOLUTION': True,
                'SET_RESOLUTION': False,
                'X_RESOLUTION': None,
                'Y_RESOLUTION': None,
                'MULTITHREADING': True,
                'OPTIONS': '',
                'DATA_TYPE': 0,
                'EXTRA': '',
                'OUTPUT': out_tif
            }

            processing.run(
                'gdal:cliprasterbymasklayer',
                clip_params,
                context=context,
                feedback=feedback,
                is_child_algorithm=True
            )
            feedback.pushInfo("  Clipping complete.")
            feedback.pushInfo("")

        finally:
            if temp_unclipped and os.path.exists(temp_unclipped):
                try:
                    os.remove(temp_unclipped)
                except Exception:
                    pass

        feedback.pushInfo("Step 6: Adding to map and applying style...")
        name = os.path.basename(out_tif)
        rlayer = QgsRasterLayer(out_tif, name)

        if not rlayer.isValid():
            feedback.reportError('  Warning: Output saved but could not be loaded in QGIS.')
        else:
            QgsProject.instance().addMapLayer(rlayer)
            feedback.pushInfo(f"  Added layer: {name}")
            band_count = rlayer.bandCount()
            feedback.pushInfo(f"  Layer has {band_count} band(s)")
            self._apply_pseudocolor_style(rlayer, var_name, band=1, feedback=feedback)

        feedback.pushInfo("")
        feedback.pushInfo("=" * 60)
        feedback.pushInfo("COMPLETE!")
        feedback.pushInfo(f"Output saved to: {out_tif}")
        if rlayer.isValid() and rlayer.bandCount() > 1:
            feedback.pushInfo("Note: Styled band 1. Use Layer Properties to view other months.")
        feedback.pushInfo("=" * 60)

        return {self.OUTPUT_TIF: out_tif}

