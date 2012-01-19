#    Copyright 2012 Rackspace, US Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License

import datetime

from lxml import etree

from nova.api.openstack import compute
from nova.api.openstack.compute.contrib import server_bandwidth
from nova.api.openstack import xmlutil
import nova.db
from nova import flags
from nova.openstack.common import jsonutils
from nova.openstack.common import timeutils
from nova import test
from nova.tests.api.openstack import fakes
from nova import utils

EXTENSION = "rax-bandwidth:bandwidth"

INSTANCE_UUID_1 = fakes.FAKE_UUID
MACADDR1 = 'aa:aa:aa:aa:aa:aa'
INFO_CACHE_1 = [{'address': MACADDR1,
                 'id': 1,
                 'network': {'label': 'meow',
                             'subnets': []}}]
INSTANCE_UUID_2 = fakes.FAKE_UUID.replace("a", "b")
MACADDR2 = 'bb:bb:bb:bb:bb:bb'
INFO_CACHE_2 = [{'address': MACADDR2,
                 'id': 2,
                 'network': {'label': 'wuff',
                             'subnets': []}}]
INSTANCE_UUID_3 = fakes.FAKE_UUID.replace("a", "c")
MACADDR3 = 'cc:cc:cc:cc:cc:cc'
MACADDR4 = 'dd:dd:dd:dd:dd:dd'
INFO_CACHE_3 = [{'address': MACADDR3,
                 'id': 3,
                 'network': {'label': 'nacho',
                             'subnets': []}},
                {'address': MACADDR4,
                 'id': 4,
                 'network': {'label': 'burrito',
                             'subnets': []}}]
FLAGS = flags.FLAGS


class ServerBandwidthTestCase(test.TestCase):
    def setUp(self):
        super(ServerBandwidthTestCase, self).setUp()

        self.fake_usages = [
                {'uuid': INSTANCE_UUID_1,
                'mac': MACADDR1,
                'start_period': datetime.datetime.utcfromtimestamp(1234),
                'last_refreshed': datetime.datetime.utcfromtimestamp(5678),
                'bw_in': 9012,
                'bw_out': 3456},

                {'uuid': INSTANCE_UUID_2,
                'mac': MACADDR2,
                'start_period': datetime.datetime.utcfromtimestamp(4321),
                'last_refreshed': datetime.datetime.utcfromtimestamp(8765),
                'bw_in': 2109,
                'bw_out': 6543},

                {'uuid': INSTANCE_UUID_3,
                'mac': MACADDR3,
                'start_period': datetime.datetime.utcfromtimestamp(9876),
                'last_refreshed': datetime.datetime.utcfromtimestamp(6789),
                'bw_in': 9875,
                'bw_out': 2366},

                {'uuid': INSTANCE_UUID_3,
                'mac': MACADDR4,
                'start_period': datetime.datetime.utcfromtimestamp(99876),
                'last_refreshed': datetime.datetime.utcfromtimestamp(99900),
                'bw_in': 99875,
                'bw_out': 22366}
         ]

        FAKE_INSTANCES = [
            fakes.stub_instance(1, uuid=INSTANCE_UUID_1,
                    nw_cache=INFO_CACHE_1),
            fakes.stub_instance(2, uuid=INSTANCE_UUID_2,
                    nw_cache=INFO_CACHE_2),
            fakes.stub_instance(3, uuid=INSTANCE_UUID_3,
                    nw_cache=INFO_CACHE_3)
        ]

        def fake_bw_usage_get_by_uuids(context, uuids, start_period):
            usages = []
            for fake in self.fake_usages:
                if fake['uuid'] in uuids:
                    usages.append(fake)
            return usages

        self.stubs.Set(nova.db, "bw_usage_get_by_uuids",
                fake_bw_usage_get_by_uuids)

        def fake_instance_get(context, id_):
            for instance in FAKE_INSTANCES:
                if id_ == instance["id"]:
                    return instance

        self.stubs.Set(nova.db, "instance_get", fake_instance_get)

        def fake_instance_get_by_uuid(context, uuid):
            for instance in FAKE_INSTANCES:
                if uuid == instance["uuid"]:
                    return instance

        self.stubs.Set(nova.db, "instance_get_by_uuid",
                fake_instance_get_by_uuid)

        def fake_instance_get_all(context, *args, **kwargs):
            return FAKE_INSTANCES

        self.stubs.Set(nova.db, "instance_get_all", fake_instance_get_all)
        self.stubs.Set(nova.db, "instance_get_all_by_filters",
                fake_instance_get_all)

        self.app = compute.APIRouter()

    def _bw_matches(self, ext, fake_usage):
        self.assertEqual(ext['audit_period_start'],
                timeutils.isotime(fake_usage['start_period']))
        self.assertEqual(ext['audit_period_end'],
                timeutils.isotime(fake_usage['last_refreshed']))
        self.assertEqual(ext['bandwidth_inbound'],
                fake_usage['bw_in'])
        self.assertEqual(ext['bandwidth_outbound'],
                fake_usage['bw_out'])

    def test_audit_period(self):
        """Confirm that we get the start and the end of the most recently
        completed audit period.  We need this to pull the bw stats.
        """
        period = utils.last_completed_audit_period(unit='month')
        (start_time, end_time) = period

        # end time should be 1st day of the current month. (UTC)
        now = datetime.datetime.utcnow()
        end = datetime.date(year=now.year, month=now.month, day=1)
        self.assertEqual(end_time.date(), end)

        prev = end - datetime.timedelta(days=1)  # rewind to previous month
        start = datetime.date(year=prev.year, month=prev.month, day=1)
        self.assertEqual(start_time.date(), start)

    def test_show_server(self):
        req = fakes.HTTPRequest.blank(
            "/fake/servers/%s" % INSTANCE_UUID_1)
        res = req.get_response(self.app)
        server_dict = jsonutils.loads(res.body)["server"]
        self.assertTrue(EXTENSION in server_dict)
        self._bw_matches(server_dict[EXTENSION][0],
                self.fake_usages[0])

    def test_detail_servers(self):
        req = fakes.HTTPRequest.blank("/fake/servers/detail")
        res = req.get_response(self.app)
        server_dicts = jsonutils.loads(res.body)["servers"]

        for server_dict in server_dicts:
            self.assertTrue(EXTENSION in server_dict)
            ext = server_dict[EXTENSION][0]
            if ext['interface'] == 'meow':
                self._bw_matches(ext, self.fake_usages[0])
            elif ext['interface'] == 'wuff':
                self._bw_matches(ext, self.fake_usages[1])
            elif ext['interface'] == 'nacho':
                self._bw_matches(ext, self.fake_usages[2])
            elif ext['interface'] == 'burrito':
                self._bw_matches(ext, self.fake_usages[3])
            else:
                self.fail("interface name (%s) didn't match" % \
                        ext['interface'])

    def test_show_xml(self):
        headers = {"accept": "application/xml"}
        req = fakes.HTTPRequest.blank(
            "/fake/servers/%s" % INSTANCE_UUID_1, headers=headers)
        res = req.get_response(self.app)

        root = etree.fromstring(res.body)
        bw = root.find("{%s}bandwidth" % server_bandwidth.XMLNS_SB)
        self.assertTrue(bw is not None)

        interfaces = bw.findall("{%s}interface" % server_bandwidth.XMLNS_SB)
        self.assertEquals(1, len(interfaces))

        interface = interfaces[0]
        self.assertEquals("meow", interface.attrib['interface'])

    def test_detail_xml(self):

        headers = {"accept": "application/xml"}
        req = fakes.HTTPRequest.blank("/fake/servers/detail", headers=headers)
        res = req.get_response(self.app)

        root = etree.fromstring(res.body)
        servers = root.findall("{%s}server" % xmlutil.XMLNS_V11)
        self.assertEquals(3, len(servers))

        server = servers[2]
        bw = server.find("{%s}bandwidth" % server_bandwidth.XMLNS_SB)
        self.assertTrue(bw is not None)

        interfaces = bw.findall("{%s}interface" % server_bandwidth.XMLNS_SB)
        self.assertEquals(2, len(interfaces))

        interface = interfaces[0]
        self.assertEquals("burrito", interface.attrib['interface'])
