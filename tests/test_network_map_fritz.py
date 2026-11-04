import pytest
from modules.network_map import NetworkMap


def mesh_fixture():
    return {'nodes':[
      {'uid':'box','device_name':'FRITZ!Box','device_model':'FRITZ!Box 6690','device_mac_address':'aa:bb:cc:00:00:01','node_interfaces':[{'uid':'b','type':'LAN','node_links':[{'node_1_uid':'box','node_2_uid':'ap','node_interface_1_uid':'b','node_interface_2_uid':'a','type':'LAN','state':'CONNECTED'}]}]},
      {'uid':'ap','device_name':'Wohnzimmer','device_mac_address':'aa:bb:cc:00:00:02','node_interfaces':[{'uid':'a','type':'LAN','node_links':[]},{'uid':'w','type':'WLAN','node_links':[{'node_1_uid':'ap','node_2_uid':'phone','node_interface_1_uid':'w','node_interface_2_uid':'p','type':'WLAN','state':'CONNECTED'}]}]},
      {'uid':'phone','device_name':'Phone','device_mac_address':'aa:bb:cc:00:00:03','node_interfaces':[{'uid':'p','type':'WLAN','node_links':[]}]},
    ]}


def test_fritz_report_merges_names_real_links_and_keeps_manual_overrides(tmp_path):
    m=NetworkMap(tmp_path)
    m.merge_lan([{'id':'old','ip':'192.168.1.20','mac':'aa:bb:cc:00:00:03','name':'192.168.1.20'}])
    m.edit_device('old',{'label':'My phone'})
    m.ingest_fritz(mesh_fixture(),[{'mac':'aa:bb:cc:00:00:01','ip':'192.168.1.1'},{'mac':'aa:bb:cc:00:00:03','ip':'192.168.1.20'}],'192.168.1.1')
    snap=m.snapshot()
    assert len(snap['devices'])==3
    assert next(d for d in snap['devices'] if d['id']=='old')['label']=='My phone'
    assert next(d for d in snap['devices'] if d['id']=='old')['name']=='Phone'
    assert next(d for d in snap['devices'] if d['id']=='old')['connection_type']=='wlan'
    assert len([e for e in snap['edges'] if e['origin']=='fritz'])==2
    assert snap['fritz_at']>0
    assert any(d.get('gateway') for d in snap['devices'])
    m.edit_device('old',{'parent':'lan:aa:bb:cc:00:00:01','connection_type':'lan'})
    m.ingest_fritz(mesh_fixture(),[],'192.168.1.1')
    assert len([e for e in m.snapshot()['edges'] if e['target']=='old' and e['origin']=='manual'])==1
    assert not any(e['origin']=='fritz' and e['target']=='old' for e in m.snapshot()['edges'])


def test_fritz_credentials_are_encrypted_and_inputs_restricted(tmp_path):
    m=NetworkMap(tmp_path)
    for value in ['8.8.8.8','localhost','http://192.168.1.1','127.0.0.1']:
        with pytest.raises(ValueError):m.configure({'fritz_host':value})
    m.configure({'fritz_host':'192.168.1.1','fritz_user':'reader','fritz_password':'private-fritz-password'})
    assert 'private-fritz-password' not in m.path.read_text()
    assert 'private-fritz-password' not in str(m.snapshot())
    assert m.snapshot()['settings']['fritz_password_saved']


def test_bad_fritz_report_preserves_existing_connections(tmp_path):
    m=NetworkMap(tmp_path)
    m.state['edges']=[{'source':'a','target':'b','origin':'fritz'}]
    with pytest.raises(ValueError):m.ingest_fritz({'error':'no data'},[],'192.168.1.1')
    assert len(m.snapshot()['edges'])==1


def test_reader_only_fetches_mesh_and_host_lists_and_rejects_off_host_paths(monkeypatch):
    import io,json
    from modules import network_map_fritz as fritz
    actions=[]
    class Response:
        status_code=200
        def __init__(self,body):self.raw=io.BytesIO(body)
        def __enter__(self):return self
        def __exit__(self,*args):pass
    class Session:
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            action=kwargs['headers']['SOAPAction'].split('#')[1].strip('"');actions.append(action)
            key='NewX_AVM-DE_MeshListPath' if 'Mesh' in action else 'NewX_AVM-DE_HostListPath'
            path='/mesh' if 'Mesh' in action else '/hosts'
            return Response(f'<root><{key}>{path}</{key}></root>'.encode())
        def get(self,url,**kwargs):
            assert url.startswith('http://192.168.1.1:49000/')
            return Response(json.dumps(mesh_fixture()).encode() if url.endswith('/mesh') else b'<List><Item><MACAddress>aa:bb:cc:00:00:01</MACAddress><IPAddress>192.168.1.1</IPAddress></Item></List>')
    monkeypatch.setattr(fritz.requests,'Session',Session)
    report,hosts=fritz.read_topology('192.168.1.1','reader','secret')
    assert len(report['nodes'])==3
    assert hosts[0]['ip']=='192.168.1.1'
    assert actions==['X_AVM-DE_GetMeshListPath','X_AVM-DE_GetHostListPath']
    class RedirectSession(Session):
        def post(self,*args,**kwargs):return Response(b'<root><NewX_AVM-DE_MeshListPath>http://8.8.8.8:49000/mesh</NewX_AVM-DE_MeshListPath></root>')
    monkeypatch.setattr(fritz.requests,'Session',RedirectSession)
    with pytest.raises(ValueError):fritz.read_topology('192.168.1.1','reader','secret')


def test_report_link_endpoints_may_be_reversed(tmp_path):
    m=NetworkMap(tmp_path);report=mesh_fixture()
    for node in report['nodes']:
        for interface in node['node_interfaces']:
            for link in interface['node_links']:
                link['node_1_uid'],link['node_2_uid']=link['node_2_uid'],link['node_1_uid']
    m.ingest_fritz(report,[{'mac':'aa:bb:cc:00:00:01','ip':'192.168.1.1'}],'192.168.1.1')
    snap=m.snapshot()
    assert next(d for d in snap['devices'] if d['name']=='Phone')['connection_type']=='wlan'
    assert any(e['source']=='lan:aa:bb:cc:00:00:02' and e['target']=='lan:aa:bb:cc:00:00:03' for e in snap['edges'])
