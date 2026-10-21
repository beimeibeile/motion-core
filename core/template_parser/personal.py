"""Personal download v1 parser (AES-256-GCM encrypted)."""

import json
import zipfile
from pathlib import Path

from .base import TemplateParser, TemplateInfo


class PersonalParser(TemplateParser):
    """Parser for personal-download-v1 format (outer 1.0.0-rc.1).

    Contains:
    - motion.json: outer manifest (plain text, contains metadata)
    - content/release.bin: AES-256-GCM encrypted content bundle
    - personal/motiongit.key.json: downloader's API Key (DO NOT EXTRACT)
    - personal/receipt.json: download receipt
    - personal/signature.json: package signature
    - assistant/motion.mjs: runtime helper (decrypts + renders)

    Security: This parser reads only the outer motion.json manifest.
    It does NOT extract or use the personal API key.
    Encrypted content (params.schema.json, scenes, etc.) is not decrypted
    at parse time; decryption happens inside the trusted motion.mjs runtime.
    """

    def parse(self) -> TemplateInfo:
        path = Path(self.path)
        with zipfile.ZipFile(path, 'r') as z:
            # Read outer motion.json (plain text manifest)
            motion_data = self._read_json(z, 'motion.json')
            if motion_data is None:
                raise ValueError(f"personal template missing motion.json: {path}")

            # Read assistant guide if exists
            guide = self._read_json(z, 'assistant/guide.json')

            # Extract metadata from outer manifest + guide
            template_id = str(motion_data.get('id', motion_data.get('templateId', '')))
            template_version = str(motion_data.get('version', ''))
            name = str(motion_data.get('name', motion_data.get('title', path.stem)))

            # If guide has template info, use it (more reliable for personal format)
            if guide and 'template' in guide:
                gt = guide['template']
                if not template_id:
                    template_id = str(gt.get('id', ''))
                if not template_version:
                    template_version = str(gt.get('version', ''))
                if name == path.stem:
                    name = str(gt.get('name', name))

            # Runtime info
            runtime = motion_data.get('runtime', {})
            runtime_profile = str(runtime.get('profile', 'motiongit-host/1'))
            runtime_entry = str(runtime.get('operation', 'motion.run'))

            # Package profile
            package_profile = str(motion_data.get('packageProfile', 'personal-download-v1'))

            # Output specs - try to get from guide or motion.json
            width = 1920
            height = 1080
            fps = 60.0
            if guide and 'template' in guide:
                t = guide['template']
                width = int(t.get('width', 1920))
                height = int(t.get('height', 1080))

            # Duration policy
            duration_policy = 'user-defined'
            duration_seconds = None

            # Services / extensions info
            services = motion_data.get('services', {})
            extensions = motion_data.get('extensions', {})

            # Detect runtime generation (v1 motion.mjs vs v2 split_root.py)
            runtime_generation = self._detect_runtime_generation(z)

            # Assets list (only non-encrypted, non-personal files)
            assets = []
            for name_in_zip in z.namelist():
                if name_in_zip.startswith('assistant/') and not name_in_zip.endswith('/'):
                    assets.append(name_in_zip)

            # Licenses
            licenses = []
            for name_in_zip in z.namelist():
                if 'LICENSE' in name_in_zip.upper() or 'THIRD-PARTY' in name_in_zip.upper():
                    if not name_in_zip.endswith('/'):
                        licenses.append(name_in_zip)

            info = TemplateInfo(
                path=str(path),
                format='personal-download-v1',
                spec_version=str(motion_data.get('specVersion', '1.0.0-rc.1')),
                template_id=template_id,
                template_version=template_version,
                name=name,
                runtime_profile=runtime_profile,
                runtime_entry=runtime_entry,
                output_width=width,
                output_height=height,
                output_fps=fps,
                duration_policy=duration_policy,
                duration_seconds=duration_seconds,
                parameters_schema=None,  # Encrypted - not available at parse time
                guide=guide,
                assets=assets,
                licenses=licenses,
                raw_motion_json=motion_data,
            )
            # Attach extra metadata for runtime routing
            info.package_profile = package_profile
            info.runtime_generation = runtime_generation
            info.has_encrypted_content = 'content/release.bin' in z.namelist()
            info.services = services
            info.extensions = extensions
            return info

    def validate(self) -> list[str]:
        errors = []
        path = Path(self.path)
        try:
            with zipfile.ZipFile(path, 'r') as z:
                names = z.namelist()
                if 'motion.json' not in names:
                    errors.append("missing motion.json")
                if 'assistant/motion.mjs' not in names:
                    errors.append("missing assistant/motion.mjs")
                if 'content/release.bin' not in names:
                    errors.append("missing content/release.bin (encrypted content)")
                if 'personal/motiongit.key.json' not in names:
                    errors.append("missing personal/motiongit.key.json")
                if 'personal/signature.json' not in names:
                    errors.append("missing personal/signature.json")
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

    @staticmethod
    def _detect_runtime_generation(z: zipfile.ZipFile) -> str:
        """Detect runtime generation from motion.mjs content.

        Returns:
            'v1': standard motion.mjs runtime (adu-driver compatible)
            'v2': split_root.py / canvas_split_root.py runtime (newer)
            'unknown': cannot determine
        """
        try:
            with z.open('assistant/motion.mjs') as f:
                # Read first 50KB to detect runtime type
                content = f.read(50000).decode('utf-8', errors='replace')
                if 'split_root' in content or 'canvas_split_root' in content:
                    return 'v2'
                if 'motion-kit' in content or 'motiongit-host' in content:
                    return 'v1'
                return 'unknown'
        except Exception:
            return 'unknown'
