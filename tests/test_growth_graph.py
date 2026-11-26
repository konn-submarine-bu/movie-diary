from __future__ import annotations

import json

from tests.conftest import text


def reply(**payload):
    return text(json.dumps(payload, ensure_ascii=False))


def create_piece(env, content, **extra):
    res = env.client.post('/api/pieces', json={'text': content, **extra})
    assert res.status_code == 201, res.text
    return res.json()['id']


def send(env, content, *, growth=False):
    env.llm.replies.append(text('我想一想。'))
    res = env.client.post('/api/pieces/chat', json={'message': content, 'use_for_growth': growth})
    assert res.status_code == 200, res.text
    return res.json()['user_message']['id']


def digest(env, signals, changes, *additional_topics):
    env.llm.replies = [reply(signals=signals)]
    if changes is not None:
        env.llm.replies.extend(reply(changes=proposals) for proposals in (changes, *additional_topics))
    res = env.client.post('/api/pieces/mind/refresh')
    assert res.status_code == 200, res.text
    return res.json()


def signal(ref, quote, topic='沉默'):
    return {'ref': ref, 'quote': quote, 'topic': topic, 'observation': quote}


def change(content, *, topic='沉默', kind='social', evidence=None, **extra):
    return {'op': 'add', 'kind': kind, 'topic': topic, 'text': content,
            'evidence': evidence or [{'signal': 1, 'relation': 'supports'}], **extra}


def state(env):
    res = env.client.get('/api/pieces/mind')
    assert res.status_code == 200, res.text
    return res.json()


def test_only_relevant_graph_features_are_loaded_and_plain_chat_is_not_queued(env):
    a = create_piece(env, '她没有回信。')
    b = create_piece(env, '我喜欢高饱和配色。')
    assert state(env)['pending'] == 2
    result = digest(env, [signal(f'piece:{a}:text', '她没有回信。'),
                          signal(f'piece:{b}:text', '高饱和配色', '配色')],
                    [change('沉默有时是拒绝。')], [change('我偏爱鲜艳的配色。', topic='配色', kind='preference',
                                                       evidence=[{'signal': 2, 'relation': 'supports'}])])
    assert {node['kind'] for node in result['graph']} == {'social', 'preference'}
    send(env, '你怎么看她的沉默？')
    prompt = env.llm.calls[-1]['messages'][0]['content']
    assert '沉默有时是拒绝。' in prompt
    assert '我偏爱鲜艳的配色。' not in prompt
    assert state(env)['pending'] == 0


def test_conversation_can_revise_but_clearing_invalidates_the_whole_revision(env):
    a = create_piece(env, '她没有回信。')
    first = digest(env, [signal(f'piece:{a}:text', '她没有回信。')], [change('沉默有时是拒绝。')])['graph'][0]
    msg = send(env, '可是她每次都给妹妹回信。', growth=True)
    revised = digest(env, [signal(f'chat:{msg}:content', '她每次都给妹妹回信')],
                     [change('只凭沉默不能判断她在拒绝。', op='revise', node_id=first['id'],
                             reason='她会回复妹妹。', evidence=[{'signal': 1, 'relation': 'counter'}])])['graph'][0]
    assert revised['replaces'] == first['id']
    assert {e['kind'] for e in revised['evidence']} == {'piece', 'chat'}
    assert env.client.delete('/api/pieces/chat').status_code == 204
    assert state(env)['graph'] == []
    assert state(env)['pending'] == 1
    send(env, '再聊聊沉默')
    assert revised['text'] not in env.llm.calls[-1]['messages'][0]['content']


def test_bare_disagreement_does_not_change_graph(env):
    a = create_piece(env, '她没有回信。')
    first = digest(env, [signal(f'piece:{a}:text', '她没有回信')], [change('沉默有时是拒绝。')])['graph']
    send(env, '我不同意。', growth=True)
    assert state(env)['pending'] == 1
    assert env.llm.replies == []
    result = env.client.post('/api/pieces/mind/refresh').json()
    assert result['pending'] == 0 and result['graph'] == first


def test_psychology_needs_multiple_sources_and_inherits_provenance(env):
    a = create_piece(env, '她没有回信。')
    first = digest(env, [signal(f'piece:{a}:text', '她没有回信')],
                   [change('我会先看她的行动。'), change('我先注意行动。', kind='psychology')])['graph']
    assert [item['kind'] for item in first] == ['social']
    b = create_piece(env, '她每次坐在窗口。')
    result = digest(env, [signal(f'piece:{b}:text', '她每次坐在窗口')],
                    [change('我通常先看具体行为，再想可能性。', kind='psychology', basis=[first[0]['id']])])
    tendency = result['mind']['psychology'][0]
    assert set(tendency['sources']) == {a, b}
    assert env.client.delete(f'/api/pieces/{a}').status_code == 204
    assert state(env)['mind']['psychology'] == []
    assert state(env)['pending'] == 1


def test_user_memory_requires_confirmation_and_can_be_forgotten(env):
    m = send(env, '聊电影先听我说感受。')
    assert state(env)['mind']['user'] == []
    r = env.client.post(f'/api/pieces/chat/{m}/remember', json={'topic': '电影'})
    assert r.status_code == 201, r.text
    memory = state(env)['mind']['user'][0]
    assert memory['text'] == '聊电影先听我说感受。'
    send(env, '聊电影吧')
    assert '用户确认的记忆：' in env.llm.calls[-1]['messages'][0]['content']
    assert env.client.delete(f"/api/pieces/mind/memories/{memory['id']}").status_code == 204
    assert state(env)['mind']['user'] == [] and state(env)['pending'] == 0
    assert env.client.get(f'/api/pieces/chat/{m}').json()['content'] == memory['text']
    send(env, '聊电影吧')
    assert '用户确认的记忆：' not in env.llm.calls[-1]['messages'][0]['content']


def test_removing_input_during_model_call_does_not_publish_stale_graph(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    a = create_piece(env, '她没有回信。')
    entered, resume = Event(), Event()
    fake = env.llm.chat

    async def delayed(messages, tools=None):
        if '提议认知图谱' in messages[0]['content']:
            entered.set()
            assert resume.wait(5)
        return await fake(messages, tools)

    monkeypatch.setattr(env.llm, 'chat', delayed)
    env.llm.replies = [reply(signals=[signal(f'piece:{a}:text', '她没有回信')]),
                       reply(changes=[change('这条不应该留下。')])]
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(env.client.post, '/api/pieces/mind/refresh')
        try:
            assert entered.wait(5)
            assert env.client.delete(f'/api/pieces/{a}').status_code == 204
        finally:
            resume.set()
        assert request.result(5).status_code == 200
    assert state(env)['graph'] == [] and state(env)['pending'] == 0


def test_worker_resumes_persisted_jobs_without_refresh(env, monkeypatch):
    from time import monotonic, sleep

    from fastapi.testclient import TestClient

    from app import mind as mind_mod
    from app.main import create_app

    a = create_piece(env, '她没有回信。')
    send(env, '不授权的旧对话。')
    assert state(env)['pending'] == 1 and len(env.llm.calls) == 1
    env.llm.replies = [reply(signals=[signal(f'piece:{a}:text', '她没有回信')]),
                       reply(changes=[change('后台形成的判断。')])]
    monkeypatch.setattr(mind_mod, 'POLL_INTERVAL', 0.05)
    with TestClient(create_app(env.settings, env.llm, env.images, env.tmdb, env.search)) as client:
        deadline = monotonic() + 5
        while monotonic() < deadline:
            result = client.get('/api/pieces/mind').json()
            if not result['pending']:
                break
            sleep(0.02)
    assert [n['text'] for n in result['graph']] == ['后台形成的判断。']
    assert '不授权的旧对话' not in env.llm.calls[-2]['messages'][1]['content']


def test_legacy_database_backfills_only_materials_and_starts_without_stale_snapshot(env):
    import sqlite3

    from fastapi.testclient import TestClient

    from app.main import create_app

    a = create_piece(env, '一条旧收藏。')
    send(env, '未授权的旧对话。')
    # Re-create the pre-graph shape: original tables and old whole-mind snapshot.
    with sqlite3.connect(env.settings.db_path) as conn:
        conn.execute('ALTER TABLE pieces DROP COLUMN growth_version')
        conn.execute('DROP TABLE growth_jobs')
        conn.execute('DROP TABLE growth_state')
        conn.execute('INSERT INTO piece_mind (id, mind, seen, updated_at) VALUES (1, ?, ?, ?)',
                     (json.dumps({'views': [{'text': '旧快照', 'sources': [a]}]}), '{}', ''))
    client = TestClient(create_app(env.settings, env.llm, env.images, env.tmdb, env.search))
    assert client.get('/api/pieces/mind').json()['graph'] == []
    assert client.get('/api/pieces/mind').json()['pending'] == 1
    assert len(client.get('/api/pieces/chat').json()['messages']) == 2
    assert client.get('/api/pieces').json()['pieces'][0]['text'] == '一条旧收藏。'


def test_reinforcement_of_same_judgment_adds_evidence_instead_of_discarding_it(env):
    a = create_piece(env, '她在走廊里沉默。')
    first = digest(env, [signal(f'piece:{a}:text', '走廊里沉默')], [change('沉默有时有代价。')])['graph'][0]
    b = create_piece(env, '她在会上没开口。')
    graph = digest(env, [signal(f'piece:{b}:text', '会上没开口')], [change('沉默有时有代价。')])['graph']
    assert len(graph) == 1 and graph[0]['replaces'] == first['id']
    assert {e['source_id'] for e in graph[0]['evidence']} == {a, b}


def test_explicit_revision_takes_precedence_over_same_batch_reinforcement(env):
    a = create_piece(env, '她在走廊里沉默。')
    first = digest(env, [signal(f'piece:{a}:text', '走廊里沉默')], [change('沉默有时有代价。')])['graph'][0]
    b = create_piece(env, '她私下跟妹妹谈了这件事。')
    result = digest(env, [signal(f'piece:{b}:text', '跟妹妹谈了这件事')],
                    [change(first['text']), change('她的沉默只发生在公开场合。', op='revise', node_id=first['id'])])
    assert result['pending'] == 0
    assert [n['text'] for n in result['graph']] == ['她的沉默只发生在公开场合。']


def test_older_user_memory_remains_visible_for_review_and_forgetting(env):
    remembered = []
    for i in range(21):
        mid = send(env, f'沟通偏好第 {i} 项。')
        response = env.client.post(f'/api/pieces/chat/{mid}/remember', json={'topic': '沟通'})
        assert response.status_code == 201
        remembered.append(response.json()['id'])
    all_ids = {n['id'] for n in state(env)['mind']['user']}
    assert all_ids == set(remembered)


def test_stale_judgment_is_not_surfaced_through_indirect_assistant_history(env):
    a = create_piece(env, '她没有回信。')
    digest(env, [signal(f'piece:{a}:text', '她没有回信')], [change('沉默可能意味着推辞。')])
    env.llm.replies.append(text('这次我理解成推辞。'))
    assert env.client.post('/api/pieces/chat', json={'message': '聊聊沉默'}).status_code == 200
    env.llm.replies.append(text('前面提到的推辞还有一个原因。'))
    assert env.client.post('/api/pieces/chat', json={'message': '换个话题'}).status_code == 200
    assert any('这次我理解成推辞' in m['content'] for m in env.llm.calls[-1]['messages'][1:])
    assert env.client.delete(f'/api/pieces/{a}').status_code == 204
    send(env, '再换个话题')
    history = [m['content'] for m in env.llm.calls[-1]['messages'] if m['role'] == 'assistant']
    assert not any('推辞' in m for m in history)


def test_invalid_model_unicode_uses_persisted_retry_path(env):
    a = create_piece(env, '她没有回信。')
    env.llm.replies = [reply(signals=[signal(f'piece:{a}:text', '她没有回信')]),
                       text(json.dumps({'changes': [change('坏字\ud800')]}))]
    res = env.client.post('/api/pieces/mind/refresh')
    assert res.status_code == 502 and res.json()['error_code'] == 'mind_update_failed'
    result = state(env)
    assert result['error'] == 'mind_update_failed' and result['retryable'] == 1 and result['graph'] == []


def test_add_carries_dependency_on_the_prior_judgment_shown_to_refinement(env):
    a = create_piece(env, '她从头到尾没开口。')
    digest(env, [signal(f'piece:{a}:text', '没开口')], [change('沉默有时是为了避开代价。')])
    b = create_piece(env, '一个人在会上没回答。')
    result = digest(env, [signal(f'piece:{b}:text', '在会上没回答')],
                    [change('有权力差时，沉默可能是在避免被孤立。')])
    assert {e['source_id'] for e in result['graph'][0]['evidence']} == {a, b}
    assert any(e['source_id'] == a and e['relation'] == 'context' for e in result['graph'][0]['evidence'])
    assert env.client.delete(f'/api/pieces/{a}').status_code == 204
    assert state(env)['graph'] == [] and state(env)['pending'] == 1
    rebuilt = digest(env, [signal(f'piece:{b}:text', '在会上没回答')], [change('她在会上没回应，动机不明。')])
    assert rebuilt['graph'][0]['text'] == '她在会上没回应，动机不明。'


def test_deleting_one_topic_does_not_invalidate_other_topic_in_same_batch(env):
    a = create_piece(env, '她一直沉默。')
    digest(env, [signal(f'piece:{a}:text', '一直沉默')], [change('我把沉默理解成拒绝。')])
    middle = create_piece(env, '她没回答会议的问题。')
    b = create_piece(env, '这张截图用了亮绿色。')
    alpha = change('有时沉默是为了回避争执。')
    beta = change('我偏爱明亮的配色。', topic='配色', kind='preference',
                  evidence=[{'signal': 2, 'relation': 'supports'}])
    env.llm.replies = [reply(signals=[signal(f'piece:{middle}:text', '没回答会议的问题'),
                                      signal(f'piece:{b}:text', '用了亮绿色', '配色')]),
                       reply(changes=[alpha, beta]), reply(changes=[beta])]
    result = env.client.post('/api/pieces/mind/refresh')
    assert result.status_code == 200
    assert any(n['text'] == beta['text'] for n in result.json()['graph'])
    assert env.client.delete(f'/api/pieces/{a}').status_code == 204
    assert any(n['text'] == beta['text'] for n in state(env)['graph'])
