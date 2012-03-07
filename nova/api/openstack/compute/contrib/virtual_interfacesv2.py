# Copyright (C) 2012 Openstack LLC.
# All Rights Reserved.
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
#    under the License.

"""The virtual interfaces extension."""

from webob import exc

from nova.api.openstack import common
from nova.api.openstack import extensions
from nova.api.openstack import wsgi
from nova.api.openstack import xmlutil
from nova import compute
from nova import exception
from nova import network
from nova.openstack.common import log as logging


LOG = logging.getLogger(__name__)
authorize = extensions.extension_authorizer('compute', 'virtual_interfaces')


vif_nsmap = {None: wsgi.XMLNS_V11}


class VirtualInterfaceTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('virtual_interfaces')
        elem = xmlutil.SubTemplateElement(root, 'virtual_interface',
                                          selector='virtual_interfaces')
        elem.set('id')
        elem.set('mac_address')
        return xmlutil.MasterTemplate(root, 1, nsmap=vif_nsmap)


def _translate_vif_summary_view(_context, vif):
    """Maps keys for VIF summary view."""
    d = {}
    d['id'] = vif['uuid']
    d['mac_address'] = vif['address']
    d['ip_addresses'] = vif['ip_addresses']
    return d


class ServerVirtualInterfaceController(object):
    """The instance VIF API controller for the OpenStack API.
    """

    def __init__(self):
        self.compute_api = compute.API()
        self.network_api = network.API()
        super(ServerVirtualInterfaceController, self).__init__()

    def _items(self, req, server_id, entity_maker):
        """Returns a list of VIFs, transformed through entity_maker."""
        context = req.environ['nova.context']

        instance = self.compute_api.get(context, server_id)
        vifs = self.network_api.get_vifs_by_instance(context, instance)
        limited_list = common.limited(vifs, req)
        res = [entity_maker(context, vif) for vif in limited_list]
        return {'virtual_interfaces': res}

    @wsgi.serializers(xml=VirtualInterfaceTemplate)
    def index(self, req, server_id):
        """Returns the list of VIFs for a given instance."""
        authorize(req.environ['nova.context'])
        return self._items(req, server_id,
                           entity_maker=_translate_vif_summary_view)

    @wsgi.serializers(xml=VirtualInterfaceTemplate)
    def create(self, req, server_id, body):
        context = req.environ["nova.context"]
        authorize(context)
        instance = self.compute_api.get(context, server_id)
        network_id = body["virtual_interface"]["network_id"]
        try:
            interfaces = self.network_api.allocate_interface_for_instance(
                    context, instance, network_id)
        except exception.AlreadyAttachedToNetwork:
            expl_str = _("Instance is already attached to network %s")
            raise exc.HTTPBadRequest(explanation=expl_str % network_id)
        except exception.NetworkOverQuota:
            expl_str = _("Too many instances attached to private network %s")
            raise exc.HTTPBadRequest(explanation=expl_str % network_id)

        self.compute_api.inject_network_info(context, instance)
        self.compute_api.reset_network(context, instance)
        return dict(virtual_interfaces=interfaces)

    @wsgi.serializers(xml=VirtualInterfaceTemplate)
    def delete(self, req, server_id, id):
        context = req.environ["nova.context"]
        authorize(context)
        instance = self.compute_api.get(context, server_id)
        interfaces = self.network_api.deallocate_interface_for_instance(
                                                context,
                                                instance,
                                                id)

        self.compute_api.inject_network_info(context, instance)
        self.compute_api.reset_network(context, instance)
        return dict(virtual_interfaces=interfaces)


class Virtual_interfacesv2(extensions.ExtensionDescriptor):
    """Virtual interface support"""

    name = "VirtualInterfaces"
    alias = "os-virtual-interfacesv2"
    namespace = ("http://docs.openstack.org/compute/ext/"
                 "virtual_interfacesv2/api/v1.1")
    updated = "2011-08-17T00:00:00+00:00"

    def get_resources(self):
        resources = []

        res = extensions.ResourceExtension(
            'os-virtual-interfacesv2',
            controller=ServerVirtualInterfaceController(),
            parent=dict(member_name='server', collection_name='servers'))
        resources.append(res)

        return resources
