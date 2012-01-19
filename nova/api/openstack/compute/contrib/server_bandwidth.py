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
from nova.compute import utils as compute_utils
from nova import db
from nova.openstack.common import timeutils
from nova import utils

ALIAS = "rax-bandwidth"
EXTENSION = "%s:bandwidth" % ALIAS
XMLNS_SB = "http://docs.rackspace.com/servers/api/ext/server_bandwidth/"


def make_bw_xml(elem):
    """ Fill in bandwidth XML under 'server' element """
    bw = xmlutil.SubTemplateElement(elem, "{%s}bandwidth" % XMLNS_SB)

    intf = xmlutil.SubTemplateElement(bw, "{%s}interface" % XMLNS_SB,
            selector="%s:bandwidth" % ALIAS)

    intf.set('interface')
    intf.set('bandwidth_outbound')
    intf.set('bandwidth_inbound')
    intf.set('audit_period_start')
    intf.set('audit_period_end')


class ServerBandwidthTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement("server")
        make_bw_xml(root)

        return xmlutil.SlaveTemplate(root, 1, nsmap={ALIAS: XMLNS_SB})


class ServersBandwidthTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement("servers")
        elem = xmlutil.SubTemplateElement(root, "server", selector="servers")
        make_bw_xml(elem)

        return xmlutil.SlaveTemplate(root, 1, nsmap={ALIAS: XMLNS_SB})


class ServerBandwidthController(wsgi.Controller):

    def _get_macs(self, context, instance):
        """Get the relevant MAC addresses for the given instance"""

        instance_uuid = instance['uuid']
        macs = {}  # dictionary mapping mac=>bw usage info

        nw_info = compute_utils.get_nw_info_for_instance(instance)
        for vif in nw_info:
            if not vif['network']:  # skip interfaces without network info
                continue
            mac = vif['address']
            if not mac:  # skip interfaces without a mac address
                continue

            macs[mac] = {'interface': vif['network']['label']}

        return (instance_uuid, macs)

    def _get_used_bw(self, context, req, servers):
        # end of last period == start of current one
        start_time = utils.last_completed_audit_period()[1]
        uuids = [server["id"] for server in servers]
        db_instances = req.get_db_instances()
        # for each uuid, get a list of the macs to report on:
        # each value is a dict mapping mac=>bw usage info
        instance_to_macs = dict([self._get_macs(context, db_instance)
                for db_instance in db_instances.itervalues()])

        # for each uuid and mac of interest, gather cached bw usage info:
        bw_interfaces = db.bw_usage_get_by_uuids(context, uuids, start_time)

        # fill in bw usage stats for each mac we care about:
        for bw_interface in bw_interfaces:
            uuid = bw_interface['uuid']
            mac = bw_interface['mac']

            if mac not in instance_to_macs.get(uuid, {}):
                # bw info was recorded for an interface that's no longer
                # present or configured:
                continue

            start_period = timeutils.isotime(bw_interface["start_period"])
            end_period = timeutils.isotime(bw_interface["last_refreshed"])

            vif_bw_usage = instance_to_macs[uuid][mac]
            vif_bw_usage.update(dict(
                    audit_period_start=start_period,
                    audit_period_end=end_period,
                    bandwidth_inbound=bw_interface["bw_in"],
                    bandwidth_outbound=bw_interface["bw_out"]))

        for server in servers:
            uuid = server['id']
            bw_usages = instance_to_macs.get(uuid, {}).values()

            # If db.bw_usage_get_by_uuids didn't return entries for
            # certain macs, we end up with usage for a mac address
            # that only has a 'interface' key due to the setup when
            # iterating through db_instances above.  So, check that we
            # have a 'audit_period_start' key and only return those.
            server[EXTENSION] = [bw_usage
                    for bw_usage in bw_usages
                            if 'audit_period_start' in bw_usage]

    def _show(self, req, resp_obj):
        if "server" in resp_obj.obj:
            context = req.environ["nova.context"]
            resp_obj.attach(xml=ServerBandwidthTemplate())
            server = resp_obj.obj["server"]
            self._get_used_bw(context, req, [server])

    @wsgi.extends
    def show(self, req, resp_obj, id):
        self._show(req, resp_obj)

    @wsgi.extends
    def detail(self, req, resp_obj):
        if "servers" in resp_obj.obj:
            context = req.environ["nova.context"]
            resp_obj.attach(xml=ServersBandwidthTemplate())
            servers = resp_obj.obj["servers"]
            self._get_used_bw(context, req, servers)


class Server_bandwidth(extensions.ExtensionDescriptor):
    """Server Bandwidth Extension"""

    name = "ServerBandwidth"
    alias = ALIAS
    namespace = XMLNS_SB
    updated = "2012-01-19T00:00:00+00:00"

    def get_controller_extensions(self):
        servers_extension = extensions.ControllerExtension(self, "servers",
                ServerBandwidthController())

        return [servers_extension]
