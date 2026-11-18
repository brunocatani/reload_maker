"""Lossless runtime projection of saved authoring; never rewrites the library."""
import copy
import json

CURVES = ('poses', 'opening', 'closing', 'firePoses', 'grabPoses')
BEHAVIOR_HEADER = ('format','version','mechanism','hkSlap','singleHandle','manualBoltFromReload','operatingAttachPoint')


def legacy_behavior(header, template):
    return dict(template=template,header={k:copy.deepcopy(header[k]) for k in BEHAVIOR_HEADER if k in header})


def runtime_profiles(source):
    if source.get('format') == 'diamond-configured-pack':
        return copy.deepcopy(source)
    if any('behavior' in p for p in source.get('profiles', [])):
        groups = {}
        for index, profile in enumerate(source['profiles']):
            behavior = profile.get('behavior') or legacy_behavior(source, source.get('defaultTemplate',''))
            key=json.dumps(behavior,sort_keys=True,separators=(',',':'))
            group=groups.setdefault(key,dict(behavior=behavior,indices=[],profiles=[]))
            group['indices'].append(index)
            group['profiles'].append({k:v for k,v in profile.items() if k!='behavior'})
        result=[]
        for group in groups.values():
            header={k:copy.deepcopy(v) for k,v in source.items() if k not in ('profiles','curves','hands',*BEHAVIOR_HEADER)}
            header.update(group['behavior']['header'])
            header['template']=group['behavior']['template']
            if 'feed' in group['behavior']:header['feed']=group['behavior']['feed']
            header['profiles']=group['profiles']
            if header['format']=='diamond-weapon-pack':
                header['slots']=[s for s in header['slots'] if s['name'] in ('Receiver','Magazine') or
                                 (s['name']=='OperatingPart' and header.get('operatingAttachPoint'))]
            if 'embeddedMagazineReceivers' in header:
                owned={p['receiver'].lower() for p in group['profiles'] if p.get('receiver','').lower()==p['magazine'].lower()}
                header['embeddedMagazineReceivers']=[r for r in header['embeddedMagazineReceivers'] if r.lower() in owned]
            result.append(dict(indices=group['indices'],document=runtime_profiles(header)))
        return dict(format='diamond-configured-pack',version=1,groups=result)
    if source.get('format') not in ('diamond-weapon-pack', 'diamond-pistol-pack'):
        return copy.deepcopy(source)
    if source.get('version') == 2:
        return copy.deepcopy(source)
    result = {k:copy.deepcopy(v) for k,v in source.items() if k != 'profiles'}
    curves, hands, curve_ids, hand_ids = [], [], {}, {}
    def reference(value, values, ids, key):
        # Exact value equality. No approximate merging, source hashes or gates.
        identity = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if identity not in ids:
            ids[identity] = len(values)
            values.append(value)
        return {key:ids[identity]}
    profiles = []
    for original in source['profiles']:
        profile = {k:copy.deepcopy(v) for k,v in original.items() if k not in ('tracks','operatingSetup','motionSource')}
        tracks = []
        for original_track in original.get('tracks', []):
            track = {k:copy.deepcopy(v) for k,v in original_track.items() if k not in CURVES}
            for field in CURVES:
                if field in original_track:
                    track[field] = reference(original_track[field], curves, curve_ids, 'curve')
            tracks.append(track)
        profile['tracks'] = tracks
        if 'fireCycle' in original:
            profile['fireCycle'] = reference(original['fireCycle'], curves, curve_ids, 'curve')
        if 'operatingFingers' in original:
            profile['operatingFingers'] = reference(original['operatingFingers'], hands, hand_ids, 'hands')
        profiles.append(profile)
    result.update(version=2, curves=curves, hands=hands, profiles=profiles)
    return result
