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

"""Server Bandwidth Extension"""

from nova.api.openstack import extensions
from nova.api.openstack import wsgi
from nova.api.openstack import xmlutil
from nova import db
from nova import utils


ALIAS = "RAX-SERVER"
EXTENSION = "%s:bandwidth" % ALIAS
XMLNS_SB = "http://docs.rackspacecloud.com/servers/api/v1.0"


class ServerBandwidthTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement("server")
        root.set(XMLNS_SB, EXTENSION)
        return xmlutil.SlaveTemplate(root, 1, nsmap={ALIAS: XMLNS_SB})


class ServersBandwidthTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement("servers")
        elem = xmlutil.SubTemplateElement(root, "server", selector="servers")
        elem.set(XMLNS_SB, EXTENSION)
        return xmlutil.SlaveTemplate(root, 1, nsmap={ALIAS: XMLNS_SB})


class ServerBandwidthController(wsgi.Controller):
    def _get_used_bw(self, context, servers):
        start_time = utils.current_audit_period()[0]
        uuids = [server["id"] for server in servers]
        bw_interfaces = db.bw_usage_get_all_by_filters(context, {"instance_id":
            uuids, "start_period": start_time})

        for server in servers:
            bw_usage = []
            for bw_interface in bw_interfaces:
                if bw_interface["instance_id"] != server["id"]:
                    continue

                interface = dict(
                        interface=bw_interface["network_label"],
                        audit_period_start=bw_interface["start_period"],
                        audit_period_end=bw_interface["last_refreshed"],
                        bandwidth_inbound=bw_interface["bw_in"],
                        bandwidth_outbound=bw_interface["bw_out"])
                bw_usage.append(interface)
            server[EXTENSION] = bw_usage

    def _show(self, req, resp_obj):
        if "server" in resp_obj.obj:
            context = req.environ["nova.context"]
            resp_obj.attach(xml=ServerBandwidthTemplate())
            server = resp_obj.obj["server"]
            self._get_used_bw(context, [server])

    @wsgi.extends
    def show(self, req, resp_obj, id):
        self._show(req, resp_obj)

    @wsgi.extends
    def detail(self, req, resp_obj):
        if "servers" in resp_obj.obj:
            context = req.environ["nova.context"]
            resp_obj.attach(xml=ServersBandwidthTemplate())
            servers = resp_obj.obj["servers"]
            self._get_used_bw(context, servers)


class Server_bandwidth(extensions.ExtensionDescriptor):
    """Server Bandwidth Extension"""

    name = "ServerBandwidth"
    alias = ALIAS
    namespace = XMLNS_SB
    updated = "2012-01-19:00:00+00:00"

    def get_controller_extensions(self):
        servers_extension = extensions.ControllerExtension(self, "servers",
                ServerBandwidthController())

        return [servers_extension]
