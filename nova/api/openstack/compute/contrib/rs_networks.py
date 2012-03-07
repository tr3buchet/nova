# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 Grid Dynamics
# Copyright 2011 OpenStack LLC.
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


from webob import exc

from nova.api.openstack import extensions
from nova import exception
from nova import flags
from nova import log as logging
from nova.rpc import common as rpc_common
import nova.network.api


FLAGS = flags.FLAGS
LOG = logging.getLogger(__name__)
authorize = extensions.extension_authorizer('compute', 'rs-networks')


def _network_call(func):
    """
    Call the network api, trying to reraise any exceptions
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except rpc_common.RemoteError as err:
            LOG.error(_("Remote Traceback: %s"), err.traceback)
            e = getattr(exception, err.exc_type, None)
            if e is None:
                raise
            raise e, err.value, None
    return wrapper


class NetworkAPIProxy(object):
    def __init__(self, network_api):
        self.api = network_api or nova.network.api.API()

    def __getattribute__(self, name):
        api = object.__getattribute__(self, 'api')
        return _network_call(object.__getattribute__(api, name))


class NetworkController(object):
    def __init__(self, network_api=None):
        self.network_api = NetworkAPIProxy(network_api)

    def index(self, req):
        context = req.environ['nova.context']
        authorize(context)
        networks = self.network_api.get_all(context)
        return  {'networks': networks}

    def show(self, req, id):
        context = req.environ['nova.context']
        authorize(context)
        LOG.debug(_("Showing network with id %s") % id)
        try:
            network = self.network_api.get(context, id)
        except exception.NetworkNotFound:
            raise exc.HTTPNotFound(_("Network not found"))
        except exception.NetworkFoundMultipleTimes:
            raise exc.HTTPNotFound(_("Network matched multiple items"))
        return {'network': network}

    def delete(self, req, id):
        context = req.environ['nova.context']
        authorize(context)
        LOG.info(_("Deleting network with id %s") % id)
        try:
            self.network_api.delete(context, id)
        except exception.NetworkNotFound:
            raise exc.HTTPNotFound(_("Network not found"))
        except exception.NetworkFoundMultipleTimes:
            raise exc.HTTPNotFound(_("Network matched multiple items"))
        except exception.NetworkBusy:
            raise exc.HTTPForbidden(_("Network has active ports"))
        return exc.HTTPAccepted()

    def create(self, req, body):
        if not body:
            raise exc.HTTPUnprocessableEntity()

        context = req.environ['nova.context']
        authorize(context)

        network = body['network']
        label = network['label']
        cidr = network['cidr']

        try:
            network = self.network_api.create(context, label, cidr)
        except Exception:
            LOG.exception(_("Create networks failed"), extra=network)
            raise exc.HTTPBadRequest("create_network failed.")

        return {'network': network[0]}


class Rs_networks(extensions.ExtensionDescriptor):
    """Admin-only Network Management Extension"""

    name = "RSNetworks"
    alias = "rs-networks"
    namespace = "http://docs.openstack.org/ext/services/api/v1.1"
    updated = "2012-03-07T09:46:43-05:00"

    def get_resources(self):
        ext = extensions.ResourceExtension('rs-networks',
                                           NetworkController())
        return [ext]
