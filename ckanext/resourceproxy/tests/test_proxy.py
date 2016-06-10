# encoding: utf-8

import sys
import requests
import json
import httpretty
import nose
from nose.tools import assert_raises

from pylons import config

import ckan.model as model
import ckan.plugins as p
import ckan.lib.create_test_data as create_test_data
import ckanext.resourceproxy.controller as controller
import ckanext.resourceproxy.plugin as proxy
from ckan.tests import helpers


JSON_STRING = json.dumps({
    "a": "foo",
    "bar": "yes, I'm proxied",
    "b": 42})


def set_resource_url(url):
    testpackage = model.Package.get('annakarenina')

    context = {
        'model': model,
        'session': model.Session,
        'user': model.User.get('testsysadmin').name,
        'use_cache': False,
    }

    resource = p.toolkit.get_action('resource_show')(
        context, {'id': testpackage.resources[0].id})
    package = p.toolkit.get_action('package_show')(
        context, {'id': testpackage.id})

    resource['url'] = url
    p.toolkit.get_action('resource_update')(context, resource)

    testpackage = model.Package.get('annakarenina')
    assert testpackage.resources[0].url == resource['url']

    return {'resource': resource, 'package': package}


class TestProxyPrettyfied(object):

    serving = False

    @classmethod
    def setup_class(cls):
        cls._original_config = config.copy()
        config['ckan.plugins'] = 'resource_proxy'
        if not p.plugin_loaded('resource_proxy'):
            p.load('resource_proxy')

        cls.app = helpers._get_test_app()
        create_test_data.CreateTestData.create()
        # Httpretty crashes with Solr on Python 2.6,
        # skip the tests
        if (sys.version_info[0] == 2 and sys.version_info[1] == 6):
            raise nose.SkipTest()

    @classmethod
    def teardown_class(cls):

        p.unload('resource_proxy')
        model.repo.rebuild_db()
        # Reenable Solr indexing
        if (sys.version_info[0] == 2 and sys.version_info[1] == 6
                and not p.plugin_loaded('synchronous_search')):
            p.load('synchronous_search')
        config.clear()
        config.update(cls._original_config)

    def setup(self):
        self.url = 'http://www.ckan.org/static/example.json'
        self.data_dict = set_resource_url(self.url)

        self.request_context = self.app.flask_app.test_request_context()
        self.request_context.push()

    def teardown(self):

        self.request_context.pop()

    def register(self, *args, **kwargs):
        httpretty.HTTPretty.register_uri(httpretty.HTTPretty.GET, *args,
                                         **kwargs)
        httpretty.HTTPretty.register_uri(httpretty.HTTPretty.HEAD, *args,
                                         **kwargs)

    @httpretty.activate
    def test_resource_proxy_on_200(self):
        self.register(
            self.url,
            content_type='application/json',
            body=JSON_STRING)

        url = self.data_dict['resource']['url']
        result = requests.get(url)
        assert result.status_code == 200, result.status_code
        assert "yes, I'm proxied" in result.content, result.content

    @httpretty.activate
    def test_resource_proxy_on_404(self):
        self.register(
            self.url,
            body="I'm not here",
            content_type='application/json',
            status=404)

        url = self.data_dict['resource']['url']
        result = requests.get(url)
        assert result.status_code == 404, result.status_code

        proxied_url = proxy.get_proxified_resource_url(self.data_dict)
        result = self.app.get(proxied_url, status='*')
        # we expect a 409 because the resourceproxy got an error (404)
        # from the server
        assert result.status_int == 409, result.status_int
        assert '404' in result.body

    @httpretty.activate
    def test_large_file(self):
        cl = controller.MAX_FILE_SIZE + 1
        self.register(
            self.url,
            content_length=cl,
            body='c' * cl)

        proxied_url = proxy.get_proxified_resource_url(self.data_dict)
        result = self.app.get(proxied_url, status='*')
        assert result.status_int == 409, result.status_int
        assert 'too large' in result.body, result.body

    @httpretty.activate
    def test_large_file_streaming(self):
        cl = controller.MAX_FILE_SIZE + 1
        self.register(
            self.url,
            streaming=True,
            body='c' * cl)

        proxied_url = proxy.get_proxified_resource_url(self.data_dict)
        result = self.app.get(proxied_url, status='*')
        assert result.status_int == 409, result.status_int
        assert 'too large' in result.body, result.body

    @httpretty.activate
    def test_invalid_url(self):
        self.data_dict = set_resource_url('http:invalid_url')

        proxied_url = proxy.get_proxified_resource_url(self.data_dict)
        result = self.app.get(proxied_url, status='*')
        assert result.status_int == 409, result.status_int
        assert 'Invalid URL' in result.body, result.body

    def test_non_existent_url(self):
        self.data_dict = set_resource_url('http://nonexistent.example.com')

        def f1():
            url = self.data_dict['resource']['url']
            requests.get(url)

        assert_raises(requests.ConnectionError, f1)

        proxied_url = proxy.get_proxified_resource_url(self.data_dict)
        result = self.app.get(proxied_url, status='*')
        assert result.status_int == 502, result.status_int
        assert 'connection error' in result.body, result.body

    def test_proxied_resource_url_proxies_http_and_https_by_default(self):
        http_url = 'http://ckan.org'
        https_url = 'https://ckan.org'

        for url in [http_url, https_url]:
            data_dict = set_resource_url(url)
            proxied_url = proxy.get_proxified_resource_url(data_dict)
            assert proxied_url != url, proxied_url

    def test_resource_url_doesnt_proxy_non_http_or_https_urls_by_default(self):
        schemes = ['file', 'ws']

        for scheme in schemes:
            url = '%s://ckan.org' % scheme
            data_dict = set_resource_url(url)
            non_proxied_url = proxy.get_proxified_resource_url(data_dict)
            proxied_url = proxy.get_proxified_resource_url(data_dict, scheme)
            assert non_proxied_url == url, non_proxied_url
            assert proxied_url != url, proxied_url
