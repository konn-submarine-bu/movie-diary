from __future__ import annotations

from app import mind as mind_mod
from tests.conftest import text
from tests.test_growth_graph import change, create_piece, digest, reply, send, signal, state


def test_empty_mind_needs_no_model_call(env):
    result = env.client.post('/api/pieces/mind/refresh').json()
    assert all(not items for items in result['mind'].values())
    assert result['pending'] == 0 and env.llm.calls == []


def test_digest_distinguishes_notes_from_authors_and_keeps_compatibility_view(env):
    a = create_piece(env, '努力不保证回报。', note='我不信')
    b = create_piece(env, '过程本身有价值。')
    result = digest(env, [signal(f'piece:{a}:text', '努力不保证回报。', '努力'),
                          signal(f'piece:{a}:note', '我不信', '努力'),
                          signal(f'piece:{b}:text', '过程本身有价值。', '努力')],
                    [change('我觉得过程本身有价值，但不保证回报。', topic='努力', evidence=[
                        {'signal': 1, 'relation': 'supports'}, {'signal': 2, 'relation': 'supports'},
                        {'signal': 3, 'relation': 'supports'}])])
    assert result['mind']['views'][0]['sources'] == [a, b]
    assert [e['relation'] for e in result['graph'][0]['evidence'] if e['field'] == 'note'] == ['context']
    prompt = env.llm.calls[0]['messages'][1]['content']
    assert '用户批注' in prompt and '原作者' in prompt and '我不信' in prompt
    assert env.llm.calls[0]['tools'] is None
    assert env.client.post('/api/pieces/mind/refresh').json()['pending'] == 0 and len(env.llm.calls) == 2


def test_invalid_quotes_invented_user_profiles_and_note_only_judgments_are_not_saved(env):
    a = create_piece(env, '故事写了沉默。', note='我有些不安')
    result = digest(env, [signal('piece:999:text', '不存在'), signal(f'piece:{a}:text', '编造的原文'),
                          signal(f'piece:{a}:note', '我有些不安')],
                    [change('小满怕沉默。'), change('用户害怕沉默。', kind='user')])
    assert result['graph'] == []
    assert '编造的原文' not in env.llm.calls[-1]['messages'][1]['content']


def test_edit_invalidates_old_version_before_redigest(env):
    a = create_piece(env, '我看见她始终沉默。')
    first = digest(env, [signal(f'piece:{a}:text', '她始终沉默')], [change('旧看法。')])['graph'][0]
    env.client.patch(f'/api/pieces/{a}', json={'text': '她后来问了一句。'})
    assert state(env)['pending'] == 1 and state(env)['graph'] == []
    assert env.client.get(f"/api/pieces/mind/nodes/{first['id']}").status_code == 404
    second = digest(env, [signal(f'piece:{a}:text', '她后来问了一句')], [change('新看法。')])['graph'][0]
    assert second['evidence'][0]['version'] == first['evidence'][0]['version'] + 1


def test_failed_refresh_keeps_unaffected_graph_and_retries(env):
    a = create_piece(env, '她没有回信。')
    digest(env, [signal(f'piece:{a}:text', '她没有回信')], [change('旧看法。')])
    create_piece(env, '一个新的故事。')
    env.llm.replies = [text('无效 JSON，含私密字样')]
    res = env.client.post('/api/pieces/mind/refresh')
    assert res.status_code == 502 and res.json()['error_code'] == 'mind_update_failed'
    assert '私密' not in res.text
    st = state(env)
    assert st['graph'][0]['text'] == '旧看法。'
    assert st['pending'] == 1 and st['error'] == 'mind_update_failed'
    assert digest(env, [], None)['pending'] == 0


def test_large_backlog_is_digested_in_bounded_batches(env, monkeypatch):
    monkeypatch.setattr(mind_mod, 'BATCH', 2)
    for i in range(5):
        create_piece(env, f'第{i}段。')
    env.llm.replies = [reply(signals=[])] * 3
    assert env.client.post('/api/pieces/mind/refresh').json()['pending'] == 0
    assert len(env.llm.calls) == 3


def test_chat_retains_voice_quote_rules_and_pending_summary(env):
    create_piece(env, '一个新收藏。')
    send(env, '你好')
    system = env.llm.calls[-1]['messages'][0]['content']
    assert '不是用户的助手' in system and '不用为了回答去翻原文，也不用报出处' in system
    assert '想核对具体原文、出处或细节时' in system and '直接引用要一字不改' in system
    assert '还有 1 处输入的变化你还没消化' in system and '当前话题没有可装载的长期判断' in system
