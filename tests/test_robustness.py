from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.main import create_app
from app.providers.demo import DemoProvider
from app.store import EventStore
from test_api import FakeEngineManager, _save_legal_demo_deck


class Engine(FakeEngineManager):
    def __init__(self):
        super().__init__();self.serial=0;self.ended=False;self.known=False;self.winner=None
    def start_match(self,event,match):
        if match.engine_game_id and match.engine_game_id not in match.engine_results and match.engine_status!='interrupted':
            return self.games[match.id]
        self.serial+=1;self.ended=False;self.known=False
        match.engine_game_id=f'table-{self.serial}';match.engine_status='active'
        game=SimpleNamespace(runtime=SimpleNamespace(table_id=match.engine_game_id))
        self.games[match.id]=game;self.match=match;return game
    def snapshot(self,match_id,player_id):
        snap=super().snapshot(match_id,player_id)
        return dict(snap,game_id=self.games[match_id].runtime.table_id,game_over=self.ended,result_known=self.known,winner_id=self.winner)
    def concede(self,match_id,player_id,**kwargs):
        self.ended=True;self.known=True
        self.winner=self.match.player_b_id if player_id==self.match.player_a_id else self.match.player_a_id
        return {'success':True}


def setup(tmp_path, count=4):
    store=EventStore(tmp_path/'events');engine=Engine();client=TestClient(create_app(DemoProvider(),store,engine))
    host=client.post('/api/events',json={'host_name':'Host','set_code':'DEMO'}).json();code=host['event']['code']
    sessions=[host]+[client.post(f'/api/events/{code}/join',json={'player_name':f'Player {i}'}).json() for i in range(count-1)]
    auth=lambda s:{'player_id':s['player_id'],'token':s['token']}
    client.post(f'/api/events/{code}/start',json=auth(host))
    for s in sessions:_save_legal_demo_deck(client,code,s)
    client.post(f'/api/events/{code}/tournament/start',json=auth(host))
    event=store.get(code)
    match=next(m for m in event.rounds[0].matches if host['player_id'] not in {m.player_a_id,m.player_b_id}) if count==4 else event.rounds[0].matches[0]
    seated={s['player_id']:s for s in sessions}
    a,b=seated[match.player_a_id],seated[match.player_b_id]
    url=f'/api/events/{code}/matches/{match.id}'
    assert client.post(url+'/game/start',json=auth(a)).status_code==200
    return client,store,engine,event,match,host,a,b,url,auth


def test_concession_results_idempotent_and_persisted(tmp_path):
    c,store,engine,event,m,host,a,b,url,auth=setup(tmp_path)
    for game in range(2):
        assert c.post(url+'/game/concede',json={**auth(b),'game_id':m.engine_game_id}).status_code==200
        for _ in range(3):assert c.get(url+'/game',params=auth(a)).status_code==200
        assert m.result.games_a==game+1
        if not game:assert c.post(url+'/game/start',json=auth(a)).status_code==200
    assert m.status=='complete'
    reloaded=EventStore(store.data_dir).get(event.code).rounds[0]
    saved=next(x for x in reloaded.matches if x.id==m.id)
    assert saved.result.games_a==2 and len(saved.engine_results)==2
    assert c.post(url+'/game/start',json=auth(a)).status_code==409
    assert c.post(url+'/game/concede',json={**auth(b),'game_id':m.engine_game_id}).status_code==409


def test_conflicting_results_require_agreement_or_host(tmp_path):
    c,store,engine,event,m,host,a,b,url,auth=setup(tmp_path)
    engine.ended=True;c.get(url+'/game',params=auth(a))
    def report(s,w):return c.post(url+'/game/result',json={**auth(s),'game_id':m.engine_game_id,'winner_id':w})
    assert report(a,a['player_id']).status_code==200
    assert report(b,b['player_id']).status_code==200
    assert not m.engine_results
    assert report(host,a['player_id']).status_code==200
    assert m.result.games_a==1
    assert report(b,b['player_id']).status_code==409
    assert report(a,a['player_id']).status_code==200
    assert m.result.games_a==1


def test_interruption_authorization_and_manual_fallback(tmp_path):
    c,store,engine,event,m,host,a,b,url,auth=setup(tmp_path)
    payload={'game_id':m.engine_game_id}
    assert c.post(url+'/game/interrupt',json={**auth(a),**payload}).status_code==403
    assert c.post(url+'/report',json={**auth(a),'games_won':2,'games_lost':0}).status_code==409
    assert c.put(f'/api/events/{event.code}/deck',json={**auth(a),'basics':{'Island':40}}).status_code==409
    assert c.post(url+'/game/interrupt',json={**auth(host),**payload}).status_code==200
    assert c.get(url+'/game',params=auth(a)).status_code==409
    assert c.post(url+'/report',json={**auth(a),'games_won':2,'games_lost':0}).status_code==200
    assert c.post(url+'/report',json={**auth(b),'games_won':2,'games_lost':0}).status_code==403


def test_private_recovery_and_public_redaction(tmp_path):
    c,store,engine,event,m,host,a,b,url,auth=setup(tmp_path)
    assert c.post(f'/api/events/{event.code}/resume',json=auth(a)).status_code==200
    assert c.post(f'/api/events/{event.code}/resume',json={'player_id':a['player_id'],'token':b['token']}).status_code==401
    public=c.get(f'/api/events/{event.code}').text
    assert a['token'] not in public and 'endpoint' not in public and 'main_copy_ids' not in public
    assert c.get(url+'/game',params=auth(host)).status_code==403


def test_partial_kit_generation_persists_before_failure(tmp_path):
    class FailsOnce(DemoProvider):
        count=0
        def choose_promo(self,*args,**kwargs):
            self.count+=1
            if self.count==2:raise ValueError('Temporary provider failure')
            return super().choose_promo(*args,**kwargs)
    store=EventStore(tmp_path/'events');p=FailsOnce();c=TestClient(create_app(p,store))
    host=c.post('/api/events',json={'host_name':'Host','set_code':'DEMO'}).json();code=host['event']['code']
    c.post(f'/api/events/{code}/join',json={'player_name':'Guest'})
    auth={'player_id':host['player_id'],'token':host['token']}
    assert c.post(f'/api/events/{code}/start',json=auth).status_code==400
    recovered=EventStore(store.data_dir)
    before=recovered.get(code).players[host['player_id']].kit.model_dump()
    c=TestClient(create_app(p,recovered))
    assert c.post(f'/api/events/{code}/start',json=auth).status_code==200
    assert recovered.get(code).players[host['player_id']].kit.model_dump()==before
