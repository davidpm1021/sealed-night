from pathlib import Path
from types import SimpleNamespace
import json
import threading
import pytest
from app.engine.service import GameEngineManager, ManagedGame, decision_id
from app.models import MatchRecord


class Bridge:
    def __init__(self, name, port):
        self.name=name; self.endpoint=f'http://127.0.0.1:{port}/mcp'; self.ended=False
        self.calls=0
    def get_game_state(self):
        return {'available':True,'players':[{'is_you':True,'name':self.name}]}
    def get_action_choices(self):
        return {'action_pending': not self.ended, 'game_over':self.ended, 'choices':[{'index':0}]}
    def choose_action(self, **kwargs):
        self.calls+=1; return {'success':False,'error':'Invalid target'}
    def concede(self): return {'success':True}


def managed(tmp_path):
    runtime=SimpleNamespace(work_root=tmp_path, stop_match=lambda _:None)
    manager=GameEngineManager(runtime=runtime)
    a,b=Bridge('seat_a',19001),Bridge('seat_b',19002)
    table=SimpleNamespace(table_id='table1',player_a=SimpleNamespace(username=a.name),player_b=SimpleNamespace(username=b.name))
    game=ManagedGame('match',table,{'a':a,'b':b})
    manager.games['match']=game; manager._save_manifest(game)
    match=MatchRecord(id='match',round_number=1,player_a_id='a',player_b_id='b',engine_game_id='table1',engine_status='active')
    return manager,game,match,a,b


def test_resume_verifies_original_seats(tmp_path,monkeypatch):
    manager,game,match,a,b=managed(tmp_path)
    bridges={a.endpoint:a,b.endpoint:b}
    monkeypatch.setattr('app.engine.service.BridgeMcpClient', lambda endpoint,**_:bridges[endpoint])
    restored=GameEngineManager(runtime=manager.runtime)
    assert restored.resume(match).restored
    assert restored.snapshot('match','a')['state']['players'][0]['name']=='seat_a'
    a.name='different_game'
    with pytest.raises(KeyError,match='could not be resumed'):
        GameEngineManager(runtime=manager.runtime).resume(match)


def test_dead_engine_does_not_silently_restart(tmp_path):
    manager,_,match,_,_=managed(tmp_path)
    fresh=GameEngineManager(runtime=manager.runtime)
    with pytest.raises(KeyError): fresh.resume(match)
    assert not fresh.games


def test_stale_duplicate_and_rejected_actions(tmp_path):
    manager,game,match,a,b=managed(tmp_path)
    with pytest.raises(ValueError,match='changed'):
        manager.choose_action('match','a',{'game_id':'old','choice':'0'})
    with pytest.raises(ValueError,match='decision changed'):
        manager.choose_action('match','a',{'decision_id':'stale','choice':'0'})
    game.commands['a']=threading.Lock();game.commands['a'].acquire()
    with pytest.raises(ValueError,match='already resolving'):
        manager.choose_action('match','a',{'choice':'0'})
    game.commands['a'].release()
    with pytest.raises(ValueError,match='Invalid target'):
        manager.choose_action('match','a',{'decision_id':decision_id(a.get_action_choices()),'choice':'0'})
    assert a.calls==1


def test_concession_survives_restart_without_live_bridge(tmp_path):
    manager,game,match,a,b=managed(tmp_path)
    manager.concede('match','a',game_id='table1')
    fresh=GameEngineManager(runtime=manager.runtime)
    fresh.resume(match)
    snap=fresh.snapshot('match','b')
    assert snap['game_over'] and snap['result_known'] and snap['winner_id']=='b'
    with pytest.raises(ValueError,match='ended'):
        fresh.concede('match','b',game_id='table1')


def test_natural_game_over_never_guesses_winner(tmp_path):
    manager,game,match,a,b=managed(tmp_path)
    a.ended=True
    snap=manager.snapshot('match','a')
    assert snap['game_over'] and not snap['result_known']
    assert json.loads(manager._manifest('match').read_text())['ended']


def test_interrupted_action_cannot_record_against_replacement(tmp_path):
    manager,game,match,a,b=managed(tmp_path)
    def interrupted():
        manager.release('match')
        replacement=ManagedGame('match',SimpleNamespace(table_id='replacement'),{'a':a,'b':b})
        manager.games['match']=replacement
        return {'success':True}
    a.concede=interrupted
    with pytest.raises(ValueError,match='interrupted'):
        manager.concede('match','a',game_id='table1')
    assert not manager.games['match'].ended
