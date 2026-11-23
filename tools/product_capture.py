"""Evaluate a deliberate save in an isolated instance of the same native editor.

The child owns its GUI/event loop and scratch workspace; it never commits an
ESP library. The parent retains authoring authority and commits its snapshot.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from product_packs import cancellation, read, require, write
from product_progress import measure, progress


def capture(request_path, backends):
    request = read(Path(request_path))
    parent = Path(request_path).resolve().parent
    # Elric still uses bounded Windows paths. Keep the native worker beneath
    # the owning workspace's short temporary root, not nested inside job/output
    # directories, which can silently leave Elric with no generated model.
    root = backends.temporary/('capture-worker-'+uuid.uuid4().hex)
    root.mkdir()
    try:
        return _capture(request, parent, root, backends)
    finally:
        require(root.parent==backends.temporary and root.name.startswith('capture-worker-'),'Unsafe capture cleanup')
        shutil.rmtree(root)


def _capture(request, parent, root, backends):
    workspace = backends.workspace
    plugin = request['plugin'].lower()
    require(Path(plugin).name == plugin and '/' not in plugin and '\\' not in plugin, 'Invalid capture plugin')
    def clone(source, target, immutable=False):
        if not source.exists(): return
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            for child in source.iterdir():
                if child.name not in ('export', '.lock'):
                    clone(child, target/child.name, immutable or child.name in ('assets','records'))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if immutable:
                try:
                    os.link(source, target)
                    return
                except OSError:
                    pass  # Different volumes/filesystems require a regular copy.
            shutil.copy2(source,target)
    for name in ('assets','workspace/hand-poses','workspace/part-grips','workspace/part-mechanisms',
                 'workspace/dependencies','local/settings/installation.json'):
        clone(workspace/name,root/name, name in ('assets','workspace/dependencies'))
    clone(workspace/'workspace/library/plugins'/plugin,root/'workspace/library/plugins'/plugin)
    executable=backends.application/'ReloadMaker.exe'
    require(executable.is_file(),'The native capture application is missing')
    process=None
    def command(method, params=None, check_cancel=True):
        if check_cancel:cancellation()
        endpoint=read(root/'local/settings/endpoint.json')
        query=dict(method=method,params=params or {},expectedSession=endpoint['session'],response='summary')
        with open('\\\\.\\pipe\\'+endpoint['pipe'],'r+b',buffering=0) as pipe:
            pipe.write(json.dumps(query).encode()+b'\n')
            data=bytearray()
            while not data.endswith(b'\n'):
                chunk=pipe.read(65536)
                require(bool(chunk),'Capture worker disconnected')
                data.extend(chunk)
        result=json.loads(data)
        require(result.get('ok'),result.get('error','Capture worker failed'))
        return result
    def ready():
        deadline=time.monotonic()+600
        while True:
            cancellation()
            require(process.poll() is None,'Native capture worker exited')
            if (root/'local/settings/endpoint.json').is_file():
                try:
                    state=command('revision')
                    if not state['busy']:return state
                except FileNotFoundError:
                    pass  # Endpoint publication can precede the first pipe instance.
            require(time.monotonic()<deadline,'Native capture worker did not finish loading within 600 seconds')
            time.sleep(.1)
    try:
        process=subprocess.Popen([str(executable),'--project',str(root),'--no-restore','--capture-worker'],
                                 cwd=backends.application,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        ready()
        weapons=sorted(request['weapons'].items(),key=lambda row:row[0]==request.get('refreshWeapon'))
        for record, authoring in weapons:
            progress('load-capture-source',weapon=record)
            # The worker has its own workspace root; a source named from the
            # owning workspace's root is given to it in full.
            package=str(authoring.get('package') or '')
            if package and not Path(package).is_absolute():authoring=dict(authoring,package=str(workspace/package))
            if request.get('operation')=='review_magazine':
                authoring=dict(authoring,showArms=True)
                authoring['holdingPreview']={**authoring.get('holdingPreview',{}),'enabled':False,'replay':False}
                for field in ('magazineGripDrafts','gripDraft','fingerDrafts'):
                    authoring.pop(field,None)
            path=root/(record.replace(':','-')+'.project.json');write(path,authoring)
            command('import_project',{'path':str(path)})
            ready()
        observed=command('state',{'fields':['source','status','lastError','recipeTracks','workspace','animation','animationSource']})
        write(parent/'capture-source-state.json',observed)
        diagnostics=command('diagnostics');write(parent/'capture-source-diagnostics.json',diagnostics)
        require(diagnostics['viewport']['validContext'],'Native capture renderer did not initialize; no source was captured')
        operation=request.get('operation','capture')
        if operation in ('prepare_pair','review_magazine'):
            output=Path(request['output']).resolve()
            require(not output.exists(),'Choose a new output directory for this operation')
            prepared=root/'part-output';prepared.mkdir()
            command('workspace',{'index':5});ready();command('simulate',{'operation':'reset'})
            if operation=='prepare_pair':
                def completed(job):
                    while True:
                        value=command('pack_job',{'operation':'state','id':job['id']})
                        if value['phase'] in ('complete','failed','cancelled'):
                            require(value['phase']=='complete',value.get('error','Asset job failed'))
                            return value['result']
                        time.sleep(.1)
                for presentation in ('full','empty'):
                    progress('prepare-'+presentation)
                    command('workspace',{'index':5});command('simulate',{'operation':'reset'})
                    command('capture_magazine_world',{'empty':presentation=='empty'})
                    model=prepared/(presentation+'-prepared.nif');hulls=prepared/(presentation+'-hulls.json')
                    completed(command('capture_prepared_part',{'output':str(model)}))
                    command('collisions',{'operation':'export-hulls','path':str(hulls)})
                    job=prepared/(presentation+'-job.json')
                    write(job,dict(format='reload-maker-world-object-job',version=1,geometry=dict(path=model.name),collision=dict(path=hulls.name)))
                    completed(command('pack_job',{'operation':'cook','job':str(job),'output':str(prepared/(presentation+'.nif'))}))
                result=dict(full=str(output/'full.nif'),empty=str(output/'empty.nif'))
            else:
                views=[]
                for hand in ('left','right'):
                    command('workspace',{'index':5});command('simulate',{'operation':'reset'})
                    command('interaction_hand',{'hand':hand});command('configure_magazine')
                    for view in ('side','top','iso'):
                        rendered=command('render',dict(view=view,fit=True,preserveCamera=True))
                        name=hand+'-'+view+'.png';shutil.copy2(rendered['path'],prepared/name)
                        rendered['path']=str(output/name);rendered['view']=view;write(prepared/(name+'.json'),rendered);views.append(rendered)
                result=dict(views=views,scope='saved-fit-contact-review',gameVerified=False)
            output.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(prepared),str(output))
            return dict(ok=True,**result)
        job=command('save_project',dict(note=request.get('note',''),refreshCurrent=bool(request.get('refreshWeapon'))))
        last=None
        with measure('capture-configurations',plugin=plugin):
            while True:
                cancellation()
                require(process.poll() is None,'Native capture worker exited during capture')
                state=command('pack_job',{'operation':'state','id':job['id']})
                if state['phase'] in ('complete','failed','cancelled'):
                    require(state['phase']=='complete',state.get('error','Capture did not complete'))
                    result=state['result']
                    break
                value=(state.get('done'),state.get('configuration'),state.get('configurations'))
                if value!=last:
                    progress('capture-configurations',plugin=plugin,done=value[0],configuration=value[1],configurations=value[2])
                    last=value
                time.sleep(.1)
        # Captures must outlive child workspace cleanup; only these files move
        # into the parent job. Source models and authoring records stay intact.
        for record, folder in result['captureFolders'].items():
            # Both sides resolved: the workspace's local folder may be a link to another drive.
            source=Path(folder).resolve();require(source.is_relative_to(root.resolve()),'Foreign capture result')
            destination=parent/'captures'/record.replace(':','-')
            destination.parent.mkdir(parents=True,exist_ok=True)
            require(not destination.exists(),'Capture result destination already exists: '+str(destination))
            shutil.move(str(source),str(destination))
            result['captureFolders'][record]=str(destination)
        return dict(ok=True,**result)
    finally:
        if process is not None and process.poll() is None:
            # Native preparation/cooking may have its own child process. Let
            # that job reach its existing cancellation boundary before removing
            # its workspace; terminating Qt first would strand the asset tool.
            try:
                if (root/'local/settings/endpoint.json').is_file():
                    state=command('pack_job',{'operation':'state'},check_cancel=False)
                    if state.get('phase') in ('running','cancelling'):
                        command('pack_job',{'operation':'cancel'},check_cancel=False)
                        deadline=time.monotonic()+600
                        while process.poll() is None:
                            state=command('pack_job',{'operation':'state'},check_cancel=False)
                            if state.get('phase') not in ('running','cancelling'):break
                            require(time.monotonic()<deadline,'Native asset cancellation did not finish')
                            time.sleep(.1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=30)
