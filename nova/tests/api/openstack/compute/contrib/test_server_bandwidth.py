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

import nova.db.api

from nova.api.openstack import compute
from nova.api.openstack.compute import extensions
from nova.api.openstack import wsgi
from nova import flags
from nova import test
from nova.tests.api.openstack import fakes
from nova import utils


EXTENSION = "RAX-SERVER:bandwidth"

INSTANCE_UUID_1 = fakes.FAKE_UUID
INSTANCE_UUID_2 = fakes.FAKE_UUID.replace("a", "b")

FLAGS = flags.FLAGS


class ServerBandwidthTestCase(test.TestCase):
    def setUp(self):
        super(ServerBandwidthTestCase, self).setUp()
        fakes.stub_out_nw_api(self.stubs)

        FAKE_INSTANCES = [
            fakes.stub_instance(1, uuid=INSTANCE_UUID_1),
            fakes.stub_instance(2, uuid=INSTANCE_UUID_2)
        ]

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

    def test_show_server(self):
        req = fakes.HTTPRequest.blank(
            "/fake/servers/%s" % INSTANCE_UUID_1)
        res = req.get_response(self.app)
        server_dict = utils.loads(res.body)["server"]

        self.assertTrue(EXTENSION in server_dict)

    def test_detail_servers(self):
        req = fakes.HTTPRequest.blank("/fake/servers/detail")
        res = req.get_response(self.app)
        server_dicts = utils.loads(res.body)["servers"]

        for server_dict in server_dicts:
            self.assertTrue(EXTENSION in server_dict)
