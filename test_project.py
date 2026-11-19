"""Persistence checks using small local projects; no clients, network or GUI."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project
from canvas import Document, Layer
from png import encode_png


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / 'project with spaces'
        self.doc = Document('A small character', 3, 2)
        self.doc.layers[0].cels[0][0:4] = bytes((255, 0, 0, 255))
        self.doc.add_frame(copy_from=0, duration=60)
        self.doc.layers.append(Layer('face', 3, 2, frames=2))
        self.doc.layers[1].opacity = 0.5
        self.doc.layers[1].visible = False
        self.doc.layers[1].reference = True
        self.doc.active = 1
        self.doc.palette = {'red': (255, 0, 0, 255)}

    def snapshot(self):
        return {str(path.relative_to(self.folder)): path.read_bytes()
                for path in self.folder.rglob('*') if path.is_file()}

    def read_manifest(self):
        return json.loads((self.folder / 'project.json').read_text(encoding='utf-8'))

    def write_manifest(self, manifest):
        (self.folder / 'project.json').write_text(json.dumps(manifest), encoding='utf-8')

    def assert_loaded(self, expected):
        actual = project.load_project(self.folder)
        self.assertEqual((actual.name, actual.w, actual.h, actual.frame, actual.active, actual.palette),
                         (expected.name, expected.w, expected.h, expected.frame, expected.active, expected.palette))
        self.assertEqual(actual.durations, expected.durations)
        self.assertEqual(len(actual.layers), len(expected.layers))
        for a, b in zip(actual.layers, expected.layers):
            self.assertEqual((a.name, a.visible, a.opacity, a.reference), (b.name, b.visible, b.opacity, b.reference))
            self.assertEqual(a.cels, b.cels)

    def test_round_trip_layers_frames_palette_and_relative_paths(self):
        saved = project.save_project(self.doc, self.folder)
        self.assertEqual(saved, str(self.folder / 'project.json'))
        self.assert_loaded(self.doc)
        self.assertEqual(project.load_project(saved, name='renamed').name, 'renamed')
        self.assertTrue(all(cel.startswith('layers/') for layer in self.read_manifest()['layers'] for cel in layer['cels']))

    def test_successful_save_preserves_unrelated_pngs_and_removes_prior_owned_cels(self):
        project.save_project(self.doc, self.folder)
        old = {cel for layer in self.read_manifest()['layers'] for cel in layer['cels']}
        unrelated = self.folder / 'layers' / 'personal-sketch.png'
        unrelated.write_bytes(encode_png(1, 1, bytes((1, 2, 3, 255))))
        untracked = self.folder / 'layers' / '00_Layer_1_f00.png'
        untracked.write_bytes(unrelated.read_bytes())
        kept = unrelated.read_bytes()
        self.doc.layers[0].cels[0][0:4] = bytes((0, 255, 0, 255))
        project.save_project(self.doc, self.folder)
        self.assert_loaded(self.doc)
        self.assertTrue(all(not (self.folder / cel).exists() for cel in old))
        self.assertEqual(unrelated.read_bytes(), kept)
        self.assertEqual(untracked.read_bytes(), kept)

    def test_interrupted_cel_write_preserves_previous_project(self):
        project.save_project(self.doc, self.folder)
        before = self.snapshot()
        self.doc.layers[0].cels[0][0:4] = bytes((0, 255, 0, 255))
        original = project.encode_png
        calls = 0
        def encode(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated disk interruption')
            return original(*args)
        with mock.patch.object(project, 'encode_png', side_effect=encode):
            with self.assertRaises(OSError):
                project.save_project(self.doc, self.folder)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(project.load_project(self.folder).layers[0].cels[0][:4], bytes((255, 0, 0, 255)))

    def test_interrupted_manifest_switch_preserves_previous_project(self):
        project.save_project(self.doc, self.folder)
        before = self.snapshot()
        with mock.patch.object(project.os, 'replace', side_effect=OSError('simulated file lock')):
            with self.assertRaises(OSError):
                project.save_project(self.doc, self.folder)
        self.assertEqual(self.snapshot(), before)
        self.assert_loaded(self.doc)

    def test_cleanup_failure_does_not_fail_committed_save(self):
        project.save_project(self.doc, self.folder)
        old = {str(self.folder / cel) for layer in self.read_manifest()['layers'] for cel in layer['cels']}
        remove = os.remove
        def retained(path):
            if str(path) in old:
                raise PermissionError('old file is temporarily locked')
            return remove(path)
        self.doc.layers[0].cels[0][0:4] = bytes((0, 255, 0, 255))
        with mock.patch.object(project.os, 'remove', side_effect=retained):
            project.save_project(self.doc, self.folder)
        self.assert_loaded(self.doc)
        self.assertTrue(all(Path(path).exists() for path in old))

    def test_interruption_immediately_after_manifest_switch_keeps_new_cels(self):
        project.save_project(self.doc, self.folder)
        replace = os.replace
        def replace_then_interrupt(source, target):
            replace(source, target)
            raise KeyboardInterrupt('simulated interruption after atomic switch')
        self.doc.layers[0].cels[0][0:4] = bytes((0, 255, 0, 255))
        with mock.patch.object(project.os, 'replace', side_effect=replace_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                project.save_project(self.doc, self.folder)
        self.assert_loaded(self.doc)

    def test_load_and_resave_original_v1_names(self):
        project.save_project(self.doc, self.folder)
        manifest = self.read_manifest()
        for li, layer in enumerate(manifest['layers']):
            for frame, cel in enumerate(layer['cels']):
                legacy = f"layers/{li:02d}_{project._safe(layer['name'])}_f{frame:02d}.png"
                (self.folder / cel).rename(self.folder / legacy)
                layer['cels'][frame] = legacy
        self.write_manifest(manifest)
        self.assert_loaded(self.doc)
        project.save_project(self.doc, self.folder)
        self.assert_loaded(self.doc)
        self.assertTrue(all(not (self.folder / cel).exists() for layer in manifest['layers'] for cel in layer['cels']))

    def test_custom_in_project_cel_names_load_but_are_not_cleaned(self):
        project.save_project(self.doc, self.folder)
        manifest = self.read_manifest()
        old = manifest['layers'][0]['cels'][0]
        custom = self.folder / 'layers' / 'hand-painted.png'
        custom.write_bytes((self.folder / old).read_bytes())
        manifest['layers'][0]['cels'][0] = 'layers\\hand-painted.png'
        self.write_manifest(manifest)
        self.assert_loaded(self.doc)
        project.save_project(self.doc, self.folder)
        self.assertTrue(custom.exists())

    def test_load_checks_dimensions_before_decoding(self):
        project.save_project(self.doc, self.folder)
        path = self.folder / self.read_manifest()['layers'][0]['cels'][0]
        path.write_bytes(encode_png(2, 2, bytes(16)))
        with mock.patch.object(project, 'decode_png') as decode:
            with self.assertRaisesRegex(ValueError, 'dimensions'):
                project.load_project(self.folder)
            decode.assert_not_called()

    def test_load_rejects_incomplete_structure_and_invalid_indices(self):
        project.save_project(self.doc, self.folder)
        original = self.read_manifest()
        cases = [('width', 0), ('height', 2049), ('durations', []), ('layers', []),
                 ('frame', -1), ('frame', 2), ('active', -1), ('active', 2)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                updated = dict(original, **{key: value})
                self.write_manifest(updated)
                with self.assertRaises(ValueError):
                    project.load_project(self.folder)

    def test_cel_count_and_invalid_opacity_rejected(self):
        project.save_project(self.doc, self.folder)
        manifest = self.read_manifest()
        manifest['layers'][0]['cels'].pop()
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, 'cel count'):
            project.load_project(self.folder)
        self.doc.layers[0].opacity = float('nan')
        with self.assertRaisesRegex(ValueError, 'opacity'):
            project.save_project(self.doc, self.folder)

    def test_save_validates_before_touching_existing_project(self):
        project.save_project(self.doc, self.folder)
        before = self.snapshot()
        self.doc.layers[0].cels[0].pop()
        with self.assertRaisesRegex(ValueError, 'data length'):
            project.save_project(self.doc, self.folder)
        self.assertEqual(self.snapshot(), before)

    def test_relative_path_validation_requires_no_file_access(self):
        self.assertEqual(project._cel_path(str(self.folder), 'layers/frame.png'), str(self.folder / 'layers/frame.png'))
        for reference in ('../frame.png', '/frame.png', 'C:/frame.png', 'C:frame.png'):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(ValueError, 'inside the project'):
                    project._cel_path(str(self.folder), reference)


if __name__ == '__main__':
    unittest.main()
