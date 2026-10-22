"""Bounded personal-grant traversal; no Google account or database required."""
import pytest
from plugin.packages.personal_import import preview_selection, resolve_selected_file
from plugin.integrations.personal_google import PersonalSourceDenied

FOLDER = 'application/vnd.google-apps.folder'
class Reader:
 def __init__(self):
  self.objects = {
   'root': dict(id='root',name='plates',mimeType=FOLDER,capabilities={'canListChildren':True}),
   'nested': dict(id='nested',name='beauty',mimeType=FOLDER,parents=['root'],capabilities={'canListChildren':True}),
   'frame': dict(id='frame',name='shot.1001.exr',mimeType='image/x-exr',parents=['nested'],size='3'),
  }
  self.reads=[]
 def get_metadata(self,id):
  self.reads.append(id)
  if id not in self.objects: raise PersonalSourceDenied('drive_object_unavailable')
  return self.objects[id]
 def list_children(self,id): return [v for v in self.objects.values() if id in v.get('parents',[])]

def test_nested_paths_are_server_derived_and_every_child_is_reread():
 r=Reader(); result=preview_selection(r,[{'drive_file_id':'root'}],max_files=10)
 assert result['scope_limited'] is True
 assert result['files']==[{'id':'frame','name':'plates/beauty/shot.1001.exr','sizeBytes':3,'root_folder_id':'root'}]
 assert 'frame' in r.reads
 meta,name=resolve_selected_file(r,'frame','root')
 assert name=='plates/beauty/shot.1001.exr' and meta['id']=='frame'

@pytest.mark.parametrize('name',['..','../outside','bad/name','bad\\name','bad\x00name'])
def test_unsafe_provider_names_are_rejected(name):
 r=Reader(); r.objects['nested']['name']=name
 with pytest.raises(PersonalSourceDenied): preview_selection(r,[{'drive_file_id':'root'}],max_files=10)

def test_folder_access_does_not_imply_child_access_or_completeness():
 r=Reader(); r.objects['root']['capabilities']['canListChildren']=False
 with pytest.raises(PersonalSourceDenied,match='folder_access_unverified'):
  preview_selection(r,[{'drive_file_id':'root'}],max_files=10)
 r=Reader(); r.objects['frame']['parents']=['outside']
 with pytest.raises(PersonalSourceDenied): resolve_selected_file(r,'frame','root')

def test_limits_cycles_shortcuts_and_case_collisions_fail_closed():
 r=Reader()
 with pytest.raises(PersonalSourceDenied,match='selection_too_large'): preview_selection(r,[{'drive_file_id':'root'}],max_files=0)
 r.objects['nested']['mimeType']='application/vnd.google-apps.shortcut'
 with pytest.raises(PersonalSourceDenied,match='unsupported_drive_object'): preview_selection(r,[{'drive_file_id':'root'}],max_files=10)
 r=Reader();r.objects['other']=dict(r.objects['frame'],id='other',name='SHOT.1001.EXR')
 with pytest.raises(PersonalSourceDenied,match='duplicate_file_name'): preview_selection(r,[{'drive_file_id':'root'}],max_files=10)
 r=Reader();r.objects['nested']['parents']=['frame']
 with pytest.raises(PersonalSourceDenied): resolve_selected_file(r,'frame','root')

def test_large_exr_selection_keeps_relative_paths_and_count_bound():
 r=Reader();del r.objects['frame']
 for frame in range(1001,1301): r.objects[str(frame)]=dict(id=str(frame),name=f'shot.{frame}.exr',mimeType='image/x-exr',parents=['nested'],size='3')
 result=preview_selection(r,[{'drive_file_id':'root'}],max_files=300)
 assert len(result['files'])==300 and all(x['name'].startswith('plates/beauty/') for x in result['files'])
 with pytest.raises(PersonalSourceDenied,match='selection_too_large'): preview_selection(r,[{'drive_file_id':'root'}],max_files=299)


class Response:
 def __init__(self, status=200, data=None, chunks=()):
  self.status_code, self.data, self.chunks, self.closed = status, data, chunks, False
 def json(self): return self.data
 def iter_content(self, chunk_size): yield from self.chunks
 def close(self): self.closed=True

class Transport:
 def __init__(self,*responses): self.responses=list(responses);self.calls=[]
 def request(self,method,url,**kwargs):
  assert method=='GET'
  self.calls.append(kwargs)
  return self.responses.pop(0)

def test_media_is_streamed_bounded_and_closed_on_success_and_failure():
 import io
 from plugin.integrations.personal_google import PersonalDriveReader
 good=Response(chunks=[b'ab',b'c']); transport=Transport(good)
 dest=io.BytesIO()
 assert PersonalDriveReader(lambda:'fixture',transport).download_to('1abcdefghijk',dest,expected_size=3)[0]==3
 assert good.closed and transport.calls[0]['stream'] is True and dest.getvalue()==b'abc'
 oversized=Response(chunks=[b'ab',b'cd']);dest=io.BytesIO()
 with pytest.raises(PersonalSourceDenied,match='source_integrity_mismatch'):
  PersonalDriveReader(lambda:'fixture',Transport(oversized)).download_to('1abcdefghijk',dest,expected_size=3)
 assert oversized.closed and dest.getvalue()==b'ab'
 denied,good=Response(401),Response(chunks=[b'a'])
 class Provider:
  def __call__(self): return 'fixture'
  def refresh_after_unauthorized(self,token): assert denied.closed
 transport=Transport(denied,good)
 PersonalDriveReader(Provider(),transport).download_to('1abcdefghijk',io.BytesIO(),expected_size=1)
 assert denied.closed and good.closed

@pytest.mark.parametrize('bad',[{'incompleteSearch':True,'files':[]},{'files':None}])
def test_folder_listing_rejects_unverified_responses(bad):
 from plugin.integrations.personal_google import PersonalDriveReader
 with pytest.raises(PersonalSourceDenied,match='folder_access_unverified'):
  PersonalDriveReader(lambda:'fixture',Transport(Response(data=bad))).list_children('1abcdefghijk')

def test_folder_listing_paginates_and_rejects_repeated_page_token():
 from plugin.integrations.personal_google import PersonalDriveReader
 transport=Transport(Response(data={'files':[{'id':'a'}],'nextPageToken':'next'}),Response(data={'files':[{'id':'b'}]}))
 assert PersonalDriveReader(lambda:'fixture',transport).list_children('1abcdefghijk')==[{'id':'a'},{'id':'b'}]
 assert transport.calls[1]['params']['pageToken']=='next'
 transport=Transport(*[Response(data={'files':[],'nextPageToken':'same'}) for _ in range(2)])
 with pytest.raises(PersonalSourceDenied,match='folder_access_unverified'):
  PersonalDriveReader(lambda:'fixture',transport).list_children('1abcdefghijk')
