"""Synthetic XML/files only: never changes the real simulator settings."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from simulator_context import capture_context,settings_path
from ball_travel import load_ball_model
from test_ball_travel import model_data


XML='''<VarXML>
<Var name="Geometry" type="list">
 <Var name="Blue Team" type="stringenum">blue_model</Var>
 <Var name="Yellow Team" type="stringenum">yellow_model</Var>
 <Var name="Length" type="double">9.000</Var>
</Var>
<Var name="Physics" type="list"><Var name="Ball" type="list">
 <Var name="Ball-ground friction" type="double">0.050</Var>
 <Var name="Ball linear damping" type="double">0.004</Var>
</Var></Var>
<Var name="Communication" type="list">
 <Var name="Command listen port" type="int">20011</Var>
 <Var name="Gaussian noise" type="list"><Var name="Noise" type="bool">false</Var></Var>
</Var>
<Var name="Viewer" type="list"><Var name="Camera" type="int">1</Var></Var>
</VarXML>'''


class SimulatorContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.xml=self.root/'settings.xml'
        self.xml.write_text(XML,encoding='utf8')
        for name in ('blue_model.ini','yellow_model.ini'):
            (self.root/name).write_text('[robot]\nradius=.09\n',encoding='utf8')

    def context(self):
        return capture_context(self.xml,self.root)['simulator_context']

    def accepted_fixture(self):
        """Fabricated test model/receipt, not exported or treated as calibration."""
        data=model_data()
        for name in data['runtime_hashes']:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'SYNTHETIC_RUNTIME_TEST_ONLY')
            data['runtime_hashes'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
        data['simulator_context']=self.context()
        report=dict(physical_acceptance_passed=True,model_id=data['model_id'],
            runtime_hashes=data['runtime_hashes'],field_settings=data['field_settings'],
            simulator_context=data['simulator_context'],fixture_only=True)
        receipt=self.root/'acceptance.json'
        receipt.write_text(json.dumps(report),encoding='utf8')
        data['acceptance_report_path']=receipt.name
        data['acceptance_report_sha256']=hashlib.sha256(receipt.read_bytes()).hexdigest()
        model=self.root/'model.json';model.write_text(json.dumps(data),encoding='utf8')
        config=dict(data['field_settings'],simulator_settings_path=str(self.xml))
        return model,data,config

    def test_physics_change_changes_fingerprint(self):
        before=self.context()
        self.xml.write_text(XML.replace('0.050','0.090'),encoding='utf8')
        self.assertNotEqual(before,self.context())

    def test_camera_and_command_port_do_not_change_physics(self):
        before=self.context()
        changed=XML.replace('>20011<','>20012<').replace('>1<','>2<')
        self.xml.write_text(changed,encoding='utf8')
        self.assertEqual(before,self.context())

    def test_robot_profile_and_noise_are_bound(self):
        before=self.context()
        (self.root/'blue_model.ini').write_text('[robot]\nradius=.10\n',encoding='utf8')
        self.assertNotEqual(before,self.context())
        before=self.context()
        self.xml.write_text(XML.replace('>false<','>true<'),encoding='utf8')
        self.assertNotEqual(before,self.context())

    def test_missing_profile_is_reported_not_live_certified(self):
        self.xml.write_text(XML.replace('blue_model','missing_model'),encoding='utf8')
        result=capture_context(self.xml,self.root)
        self.assertEqual(result['missing_profile_files'],['missing_model.ini'])
        self.assertFalse(result['live_settings_verified'])

    def test_relative_implicit_config_source_is_rejected(self):
        for value in (None,'','somewhere.xml',123):
            with self.subTest(value=value),self.assertRaises(ValueError):
                settings_path({'simulator_settings_path':value})

    def test_bad_xml_or_nonfinite_physics_rejected(self):
        for xml in ('<VarXML/>',XML.replace('0.050','nan')):
            self.xml.write_text(xml,encoding='utf8')
            with self.assertRaises(ValueError):self.context()

    def test_accepted_fixture_loads_and_changed_physics_fails_at_startup(self):
        path,data,config=self.accepted_fixture()
        model=load_ball_model(path,self.root,config)
        self.assertTrue(model.evaluate(0,4500,1000,contact=(100,0)).reachable)
        self.xml.write_text(XML.replace('0.050','0.090'),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'physical context mismatch'):
            load_ball_model(path,self.root,config)

    def test_new_model_context_cannot_borrow_old_receipt(self):
        path,data,config=self.accepted_fixture()
        self.xml.write_text(XML.replace('0.050','0.090'),encoding='utf8')
        data['simulator_context']=self.context()
        path.write_text(json.dumps(data),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'physical/field context'):
            load_ball_model(path,self.root,config)

    def test_runtime_change_fails_at_startup(self):
        path,data,config=self.accepted_fixture()
        (self.root/'grSim.exe').write_bytes(b'SYNTHETIC_CHANGED_BINARY')
        with self.assertRaisesRegex(ValueError,'runtime/field context mismatch'):
            load_ball_model(path,self.root,config)

    def test_saved_physics_change_after_startup_blocks_each_kick_evaluation(self):
        path,data,config=self.accepted_fixture()
        model=load_ball_model(path,self.root,config)
        self.xml.write_text(XML.replace('0.050','0.090'),encoding='utf8')
        self.assertEqual(model.evaluate(0,4500,1000).reason,'MODEL_PHYSICS_CONTEXT_MISMATCH')

    def test_saved_settings_disappearing_after_startup_blocks_evaluation(self):
        path,data,config=self.accepted_fixture()
        model=load_ball_model(path,self.root,config)
        # Use a context reader for a missing synthetic file; no real files removed.
        model.context_reader=lambda:capture_context(self.root/'absent.xml',self.root)['simulator_context']
        self.assertEqual(model.evaluate(0,4500,1000).reason,'MODEL_PHYSICS_CONTEXT_UNAVAILABLE')

    def test_usable_model_cannot_accept_unknown_selected_robot_profile(self):
        path,data,config=self.accepted_fixture()
        self.xml.write_text(XML.replace('blue_model','missing_model'),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'robot profile unresolved'):
            load_ball_model(path,self.root,config)
