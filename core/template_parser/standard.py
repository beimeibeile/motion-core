"""Standard format parser (plain text motion.json)."""

import json
import zipfile
from pathlib import Path

from .base import TemplateParser, TemplateInfo


class StandardParser(TemplateParser):
    """Parser for standard .motion format (specVersion 0.1.0-draft.1).

    Standard format is a plain ZIP containing:
    - motion.json: template manifest
    - params.schema.json: parameter schema
    - adapter.py: Python runtime entry
    - render.mjs: Node.js renderer
    - core/: template source (HTML/JS/CSS/scenes/fonts)
    """

    def parse(self) -> TemplateInfo:
        path = Path(self.path)
        with zipfile.ZipFile(path, 'r') as z:
            # Read motion.json
            motion_data = self._read_json(z, 'motion.json')
            if motion_data is None:
                raise ValueError(f"standard template missing motion.json: {path}")

            # Read params.schema.json
            params_schema = self._read_json(z, 'params.schema.json')

            # Read guide if exists
            guide = self._read_json(z, 'guide.json')

            # Extract metadata from 'template' field
            template_meta = motion_data.get('template', {})
            template_id = str(template_meta.get('id', motion_data.get('id', '')))
            template_version = str(template_meta.get('version', motion_data.get('version', '')))
            name = str(template_meta.get('name', motion_data.get('name', path.stem)))

            # Runtime info
            runtime = motion_data.get('runtime', {})
            runtime_profile = str(runtime.get('profile', 'standard'))
            runtime_entry = str(runtime.get('entry', 'adapter.py'))

            # Output specs
            output = motion_data.get('output', {})
            width = int(output.get('width', 1920))
            height = int(output.get('height', 1080))
            fps_raw = output.get('fps', 60)
            if isinstance(fps_raw, dict):
                fps = float(fps_raw.get('num', 60)) / float(fps_raw.get('den', 1))
            else:
                fps = float(fps_raw)

            # Duration
            duration_policy = str(output.get('durationPolicy', 'user-defined'))
            duration_seconds = output.get('durationSeconds')

            # Assets list
            assets = []
            for name_in_zip in z.namelist():
                if name_in_zip.startswith('core/assets/') or name_in_zip.startswith('core/fonts/'):
                    if not name_in_zip.endswith('/'):
                        assets.append(name_in_zip)

            # Licenses
            licenses = []
            for name_in_zip in z.namelist():
                if name_in_zip.startswith('licenses/') and not name_in_zip.endswith('/'):
                    licenses.append(name_in_zip)

            return TemplateInfo(
                path=str(path),
                format='standard',
                spec_version=str(motion_data.get('specVersion', '0.1.0-draft.1')),
                template_id=template_id,
                template_version=template_version,
                name=name,
                runtime_profile=runtime_profile,
                runtime_entry=runtime_entry,
                output_width=width,
                output_height=height,
                output_fps=fps,
                duration_policy=duration_policy,
                duration_seconds=float(duration_seconds) if duration_seconds else None,
                parameters_schema=params_schema,
                guide=guide,
                assets=assets,
                licenses=licenses,
                raw_motion_json=motion_data,
            )

    def validate(self) -> list[str]:
        errors = []
        path = Path(self.path)
        try:
            with zipfile.ZipFile(path, 'r') as z:
                names = z.namelist()
                if 'motion.json' not in names:
                    errors.append("missing motion.json")
                if 'adapter.py' not in names and 'render.mjs' not in names:
                    errors.append("missing runtime entry (adapter.py or render.mjs)")
                if 'params.schema.json' not in names:
                    errors.append("missing params.schema.json")
        except zipfile.BadZipFile:
            errors.append(f"not a valid ZIP file: {path}")
        except Exception as e:
            errors.append(f"validation error: {e}")
        return errors

    @staticmethod
    def _read_json(z: zipfile.ZipFile, name: str) -> dict | None:
        try:
            with z.open(name) as f:
                return json.loads(f.read().decode('utf-8'))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return None
