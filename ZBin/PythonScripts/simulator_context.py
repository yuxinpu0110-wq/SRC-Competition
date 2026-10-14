"""Fingerprint recorded grSim physical settings, not Client/UI/network state.

The local grSim uses the user's .grsim.xml as observed in our diagnostics.
This reads saved settings only: it does NOT claim to query live ODE parameters.
Before calibration, save settings and restart grSim using this configuration;
do not change physical settings in the running GUI during a calibrated trial.
"""
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def valid_digest(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def validate_context(context):
    if (not isinstance(context,dict) or type(context.get('schema_version')) is not int
            or context['schema_version']!=1 or context.get('settings_source')!='grsim_varxml'
            or not valid_digest(context.get('physical_settings_sha256'))
            or not isinstance(context.get('robot_profile_hashes'),dict)):
        raise ValueError('Missing/invalid saved simulator physical context')
    for name,digest in context['robot_profile_hashes'].items():
        if (not isinstance(name,str) or Path(name).name!=name or ':' in name or '\\' in name
                or not name.endswith('.ini') or not valid_digest(digest)):
            raise ValueError('Invalid simulator robot-profile fingerprint')


def settings_path(config):
    value=config.get('simulator_settings_path')
    if value=='@user-grsim':
        return Path.home()/'.grsim.xml'
    if not isinstance(value,str) or not value or not Path(value).is_absolute():
        raise ValueError('simulator_settings_path must be @user-grsim or an explicit absolute XML path')
    return Path(value)


def capture_context(path,runtime_root):
    """Return portable semantic fingerprint plus human-readable provenance.

    All geometry/physics and communication noise/vanishing settings are covered.
    Selected robot-profile INI files are hashed when present; missing profiles
    are reported explicitly (compiled defaults remain bound by grSim.exe hash).
    """
    path=Path(path).resolve();runtime_root=Path(runtime_root).resolve()
    raw=path.read_bytes()
    root=ET.fromstring(raw)
    if root.tag!='VarXML': raise ValueError('Expected grSim VarXML physical settings')
    values={}
    def walk(node,prefix):
        for child in node.findall('Var'):
            name=child.get('name')
            if not name: raise ValueError('Unnamed simulator setting')
            key=prefix+'/'+name if prefix else name
            if key in values: raise ValueError('Duplicate simulator setting path')
            kind=child.get('type','')
            if kind=='list':
                walk(child,key)
            else:
                value=(child.text or '').strip()
                if kind in ('double','int'):
                    value=float(value)
                    if not math.isfinite(value): raise ValueError('Non-finite simulator parameter')
                    if kind=='int':
                        if value!=int(value): raise ValueError('Fractional integer simulator parameter')
                        value=int(value)
                elif kind=='bool':
                    if value not in ('true','false'): raise ValueError('Invalid simulator boolean')
                    value=value=='true'
                values[key]=dict(type=kind,value=value)
    walk(root,'')
    if not any(k.startswith('Geometry/') for k in values) or not any(k.startswith('Physics/') for k in values):
        raise ValueError('Simulator settings missing geometry or physics')
    physical={k:v for k,v in values.items() if k.startswith(('Geometry/','Physics/'))
              or (k.startswith('Communication/') and any(t in k.lower() for t in ('noise','vanish')))}
    profiles={};missing=[]
    for team in ('Blue Team','Yellow Team'):
        profile=values.get('Geometry/'+team,{}).get('value')
        if not isinstance(profile,str) or not profile: raise ValueError('Missing simulator robot profile')
        # This official local distribution lists root ZBin INIs as profiles.
        name=profile+'.ini'
        if Path(name).name!=name or ':' in name or '\\' in name:
            raise ValueError('Simulator profile must be a local basename')
        file=(runtime_root/name).resolve()
        if not file.is_relative_to(runtime_root): raise ValueError('Profile escapes runtime directory')
        if file.is_file(): profiles[name]=sha256(file.read_bytes())
        else: missing.append(name)
    context=dict(schema_version=1,settings_source='grsim_varxml',
        physical_settings_sha256=sha256(json.dumps(physical,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf8')),
        robot_profile_hashes=profiles)
    validate_context(context)
    return dict(simulator_context=context,settings_path=str(path),settings_file_sha256=sha256(raw),
                physical_settings=physical,missing_profile_files=sorted(set(missing)),
                live_settings_verified=False,
                limitation='Saved settings fingerprint only; save and restart simulator before calibration. Live GUI edits cannot be detected until persisted.')
