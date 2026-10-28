"""Replay the real UE single-animation prop name at the importer's mapping seam."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from unreal_import_world_props import map_clips


class Sequence:
    def __init__(self, name, skeleton, duration=.03333333507180214):
        self.name, self.skeleton, self.duration = name, skeleton, duration

    def get_name(self):
        return self.name

    def get_path_name(self):
        return '/Game/Original/World/Props/10117_pxfraffy/' + self.name

    def get_editor_property(self, name):
        return {'skeleton': self.skeleton, 'sequence_length': self.duration}[name]


class OriginalPropAnimations(unittest.TestCase):
    def setUp(self):
        self.skeleton = object()
        self.model = {'clips': [{'name': 'FXTESTG\\IDLE', 'glb_name': 'FXTESTG_IDLE',
                                 'duration': .03333333507180214}]}

    def test_single_clip_uses_observed_source_anim_name(self):
        # Saved by UE in world-import-props-20261025-175634-065005.log.
        sequence = Sequence('10117_pxfraffy_Anim', self.skeleton)
        mapped = map_clips(self.model, [sequence], self.skeleton, '10117_pxfraffy')
        self.assertIs(mapped['FXTESTG\\IDLE'], sequence)

    def test_source_anim_name_is_not_a_multi_clip_fallback(self):
        model = copy.deepcopy(self.model)
        model['clips'].append({'name': 'SECOND\\IDLE', 'glb_name': 'SECOND_IDLE', 'duration': .03333333507180214})
        sequences = [Sequence('10117_pxfraffy_Anim', self.skeleton),
                     Sequence('10117_pxfraffySECOND_IDLE', self.skeleton)]
        with self.assertRaisesRegex(RuntimeError, 'identity/skeleton'):
            map_clips(model, sequences, self.skeleton, '10117_pxfraffy')

    def test_foreign_source_or_skeleton_is_rejected(self):
        for sequence in [Sequence('other_Anim', self.skeleton), Sequence('10117_pxfraffy_Anim', object())]:
            with self.subTest(name=sequence.name):
                with self.assertRaisesRegex(RuntimeError, 'identity/skeleton'):
                    map_clips(self.model, [sequence], self.skeleton, '10117_pxfraffy')

    def test_nonfinite_and_changed_durations_are_rejected(self):
        for duration in [float('nan'), float('inf'), float('-inf'), 5.0]:
            with self.subTest(duration=duration):
                sequence = Sequence('10117_pxfraffyFXTESTG_IDLE', self.skeleton, duration)
                with self.assertRaisesRegex(RuntimeError, 'duration changed'):
                    map_clips(self.model, [sequence], self.skeleton, '10117_pxfraffy')

    def test_existing_exact_multi_clip_names_still_match(self):
        sequence = Sequence('10117_pxfraffyFXTESTG_IDLE', self.skeleton)
        self.assertIs(map_clips(self.model, [sequence], self.skeleton, '10117_pxfraffy')['FXTESTG\\IDLE'], sequence)


if __name__ == '__main__':
    unittest.main()
