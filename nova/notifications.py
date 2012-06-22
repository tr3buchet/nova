# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2012 OpenStack, LLC.
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

"""Functionality related to notifications common to multiple layers of
the system.
"""

import nova.context
from nova import db
from nova import exception
from nova import flags
from nova import log
from nova import network
from nova.network import model as network_model
from nova.notifier import api as notifier_api
from nova.openstack.common import cfg
from nova.openstack.common import timeutils
from nova import utils

LOG = log.getLogger(__name__)

notify_state_opt = cfg.StrOpt('notify_on_state_change', default=None,
    help='If set, send compute.instance.update notifications on instance '
         'state changes.  Valid values are None for no notifications, '
         '"vm_state" for notifications on VM state changes, or '
         '"vm_and_task_state" for notifications on VM and task state '
         'changes.')

FLAGS = flags.FLAGS
FLAGS.register_opt(notify_state_opt)


def send_update(context, old_instance, new_instance, service=None, host=None):
    """Send compute.instance.update notification to report changes
    in vm state and (optionally) task state
    """

    send_update_with_states(context, new_instance, old_instance["vm_state"],
            new_instance["vm_state"], old_instance["task_state"],
            new_instance["task_state"], service, host)


def send_update_with_states(context, instance, old_vm_state, new_vm_state,
        old_task_state, new_task_state, service=None, host=None):
    """Send compute.instance.update notification to report changes
    in vm state and (optionally) task state
    """

    if not FLAGS.notify_on_state_change:
        # skip all this if state updates are disabled
        return

    fire_update = False

    if old_vm_state != new_vm_state:
        # yes, the vm state is changing:
        fire_update = True
    elif (FLAGS.notify_on_state_change.lower() == "vm_and_task_state" and
          old_task_state != new_task_state):
        # yes, the task state is changing:
        fire_update = True

    if fire_update:
        try:
            _send_instance_update_notification(context, instance, old_vm_state,
                    old_task_state, new_vm_state, new_task_state, service,
                    host)
        except Exception:
            LOG.exception(_("Failed to send state update notification"),
                    instance=instance)


def _send_instance_update_notification(context, instance, old_vm_state,
        old_task_state, new_vm_state, new_task_state, service=None, host=None):
    """Send 'compute.instance.exists' notification to inform observers
    about instance state changes"""

    payload = usage_from_instance(context, instance, None, None)

    states_payload = {
        "old_state": old_vm_state,
        "state": new_vm_state,
        "old_task_state": old_task_state,
        "new_task_state": new_task_state,
    }

    payload.update(states_payload)

    # add audit fields:
    (audit_start, audit_end) = audit_period_bounds(current_period=True)
    payload["audit_period_beginning"] = audit_start
    payload["audit_period_ending"] = audit_end

    # add bw usage info:
    bw = bandwidth_usage(instance, audit_start)
    payload["bandwidth"] = bw

    try:
        system_metadata = db.instance_system_metadata_get(
                context, instance.uuid)
    except exception.NotFound:
        system_metadata = {}

    # add image metadata
    image_meta_props = image_meta(system_metadata)
    payload["image_meta"] = image_meta_props

    # if the service name (e.g. api/scheduler/compute) is not provided, default
    # to "compute"
    if not service:
        service = "compute"

    publisher_id = notifier_api.publisher_id(service, host)

    notifier_api.notify(context, publisher_id, 'compute.instance.update',
            notifier_api.INFO, payload)


def audit_period_bounds(current_period=False):
    """Get the start and end of the relevant audit usage period

    :param current_period: if True, this will generate a usage for the
        current usage period; if False, this will generate a usage for the
        previous audit period.
    """

    begin, end = utils.last_completed_audit_period()
    if current_period:
        audit_start = end
        audit_end = timeutils.utcnow()
    else:
        audit_start = begin
        audit_end = end

    return (audit_start, audit_end)


def bandwidth_usage(instance_ref, audit_start,
        ignore_missing_network_data=True):
    """Get bandwidth usage information for the instance for the
    specified audit period.
    """

    admin_context = nova.context.get_admin_context(read_deleted='yes')

    if (instance_ref.get('info_cache') and
        instance_ref['info_cache'].get('network_info')):

        cached_info = instance_ref['info_cache']['network_info']
        nw_info = network_model.NetworkInfo.hydrate(cached_info)
    else:
        try:
            nw_info = network.API().get_instance_nw_info(admin_context,
                    instance_ref)
        except Exception:
            LOG.exception('Failed to get nw_info', instance=instance_ref)
            if ignore_missing_network_data:
                return
            raise

    macs = [vif['address'] for vif in nw_info]
    uuids = [instance_ref["uuid"]]

    bw_usages = db.bw_usage_get_by_uuids(admin_context, uuids, audit_start)
    bw_usages = [b for b in bw_usages if b.mac in macs]

    bw = {}

    for b in bw_usages:
        label = 'net-name-not-found-%s' % b['mac']
        for vif in nw_info:
            if vif['address'] == b['mac']:
                label = vif['network']['label']
                break

        bw[label] = dict(bw_in=b.bw_in, bw_out=b.bw_out)

    return bw


def image_meta(system_metadata):
    """Format image metadata for use in notifications from the instance
    system metadata.
    """
    image_meta = {}
    for md_key, md_value in system_metadata.iteritems():
        if md_key.startswith('image_'):
            image_meta[md_key[6:]] = md_value

    return image_meta


def usage_from_instance(context, instance_ref, network_info,
                system_metadata, **kw):
    """Get usage information for an instance which is common to all
    notifications.

    :param network_info: network_info provided if not None
    :param system_metadata: system_metadata DB entries for the instance,
    if not None.  *NOTE*: Currently unused here in trunk, but needed for
    potential custom modifications.
    """

    def null_safe_str(s):
        return str(s) if s else ''

    image_ref_url = utils.generate_image_url(instance_ref['image_ref'])

    instance_type_name = instance_ref.get('instance_type', {}).get('name', '')

    if system_metadata is None:
        try:
            system_metadata = db.instance_system_metadata_get(
                    context, instance_ref['uuid'])
        except exception.NotFound:
            system_metadata = {}

    # Provide base_image_ref so RAX knows what image the instance
    # was built from initially
    base_image_ref = system_metadata.get('image_base_image_ref')
    if base_image_ref:
        base_image_ref_url = utils.generate_image_url(base_image_ref)
    else:
        base_image_ref_url = ''

    usage_info = dict(
        # Owner properties
        tenant_id=instance_ref['project_id'],
        user_id=instance_ref['user_id'],

        # Identity properties
        instance_id=instance_ref['uuid'],
        display_name=instance_ref['display_name'],
        reservation_id=instance_ref['reservation_id'],

        # Type properties
        instance_type=instance_type_name,
        instance_type_id=instance_ref['instance_type_id'],
        architecture=instance_ref['architecture'],

        # Capacity properties
        memory_mb=instance_ref['memory_mb'],
        disk_gb=instance_ref['root_gb'] + instance_ref['ephemeral_gb'],
        vcpus=instance_ref['vcpus'],
        # Note(dhellmann): This makes the disk_gb value redundant, but
        # we are keeping it for backwards-compatibility with existing
        # users of notifications.
        root_gb=instance_ref['root_gb'],
        ephemeral_gb=instance_ref['ephemeral_gb'],

        # Location properties
        host=instance_ref['host'],
        availability_zone=instance_ref['availability_zone'],

        # Date properties
        created_at=str(instance_ref['created_at']),
        # Nova's deleted vs terminated instance terminology is confusing,
        # this should be when the instance was deleted (i.e. terminated_at),
        # not when the db record was deleted. (mdragon)
        deleted_at=null_safe_str(instance_ref.get('terminated_at')),
        launched_at=null_safe_str(instance_ref.get('launched_at')),

        # Image properties
        image_ref_url=image_ref_url,
        os_type=instance_ref['os_type'],
        kernel_id=instance_ref['kernel_id'],
        ramdisk_id=instance_ref['ramdisk_id'],

        # FIXME(comstud): Remove this when we're sure that billing is
        # not using it (original_image_ref)
        original_image_ref_url=base_image_ref_url,
        base_image_ref_url=base_image_ref_url,

        # Status properties
        state=instance_ref['vm_state'],
        state_description=null_safe_str(instance_ref.get('task_state')),
        )

    if network_info is not None:
        usage_info['fixed_ips'] = network_info.fixed_ips()

    usage_info.update(kw)
    return usage_info
