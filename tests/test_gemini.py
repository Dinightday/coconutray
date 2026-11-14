from dataclasses import replace
from unittest.mock import patch
import pytest

from coconutray import gemini
from coconutray.query import interpret_query


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-key-never-sent')
    monkeypatch.setenv('COCONUTRAY_GEMINI_ENABLED','true')
    monkeypatch.setenv('COCONUTRAY_GEMINI_FREE_TIER_CONFIRMED','true')
    gemini._minute_calls.clear();gemini._day_calls=0;gemini._cooldown_until=0


QUERY='Quero encontrar uma mochila executiva preta impermeável até R$ 180'


def test_simple_search_does_not_spend_model_request():
    with patch('coconutray.gemini._generate') as call:
        result=interpret_query('Mochila preta',True)
    call.assert_not_called();assert result.engine=='text'


def test_semantic_category_is_used_but_all_mandatory_filters_survive():
    with patch('coconutray.gemini._generate',return_value={'category':'mochila','attributes':[{'name':'cor','value':'azul','evidence':'preta'}],'max_price':999}):
        result=interpret_query(QUERY,True)
    assert result.engine=='gemini' and result.catalog_query=='mochila'
    assert result.constraints=={'cor':'preto','impermeavel':'sim'} and str(result.max_price)=='180'
    assert 'executiva' in result.terms


def test_fabricated_category_is_not_used():
    with patch('coconutray.gemini._generate',return_value={'category':'computador','attributes':[],'max_price':None}):
        result=interpret_query(QUERY,True)
    assert result.search_terms==result.terms


def test_missing_key_or_unconfirmed_free_tier_does_not_call(monkeypatch):
    monkeypatch.setenv('COCONUTRAY_GEMINI_FREE_TIER_CONFIRMED','false')
    with patch('coconutray.gemini._generate') as call:
        assert interpret_query(QUERY,True).engine=='text'
    call.assert_not_called()


@pytest.mark.parametrize('suffix',[' meu email e contato@example.com',' telefone 11999999999',' https://site.example',' meu nome é Ana',' entregar na rua das Flores'])
def test_contact_information_is_not_sent_to_model(suffix):
    with patch('coconutray.gemini._generate') as call:
        interpret_query(QUERY+suffix,True)
    call.assert_not_called()


def test_timeout_and_quota_keep_basic_search():
    class QuotaError(Exception): code=429
    baseline=interpret_query(QUERY)
    with patch('coconutray.gemini._generate',side_effect=QuotaError):
        fallback=interpret_query(QUERY,True)
    assert fallback.terms==baseline.terms and fallback.constraints==baseline.constraints
    assert fallback.warnings and gemini._cooldown_until>0
    with patch('coconutray.gemini._generate') as call:
        assert interpret_query(QUERY,True).engine=='text'
    call.assert_not_called()


def test_local_rate_budget_stops_calls(monkeypatch):
    monkeypatch.setenv('COCONUTRAY_GEMINI_RPM','1')
    with patch('coconutray.gemini._generate',return_value={'category':'mochila','attributes':[],'max_price':None}) as call:
        interpret_query(QUERY,True);result=interpret_query(QUERY,True)
    assert call.call_count==1 and result.engine=='text' and result.warnings


def test_invalid_budget_configuration_falls_back_without_remote_call(monkeypatch):
    monkeypatch.setenv('COCONUTRAY_GEMINI_RPM','invalid')
    with patch('coconutray.gemini._generate') as call:
        assert interpret_query(QUERY,True).engine=='text'
    call.assert_not_called()
