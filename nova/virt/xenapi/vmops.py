# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2010 Citrix Systems, Inc.
# Copyright 2010 OpenStack LLC.
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

"""
Management class for VM-related functions (spawn, reboot, etc).
"""

import base64
import binascii
import cPickle as pickle
import functools
import os
import random
import time
import uuid

from eventlet import greenthread

from nova.compute import api as compute
from nova.compute import power_state
from nova import context as nova_context
from nova import db
from nova import exception
from nova import flags
from nova import log as logging
from nova.openstack.common import cfg
from nova.openstack.common import importutils
from nova.openstack.common import jsonutils
from nova.openstack.common import timeutils
from nova import utils
from nova.virt import driver
from nova.virt.xenapi import firewall
from nova.virt.xenapi import network_utils
from nova.virt.xenapi import vm_utils
from nova.virt.xenapi import volume_utils


LOG = logging.getLogger(__name__)

xenapi_vmops_opts = [
    cfg.IntOpt('agent_version_timeout',
               default=300,
               help='number of seconds to wait for agent '
                    'to be fully operational'),
    cfg.IntOpt('xenapi_running_timeout',
               default=60,
               help='number of seconds to wait for instance '
                    'to go to running state'),
    cfg.StrOpt('xenapi_vif_driver',
               default='nova.virt.xenapi.vif.XenAPIBridgeDriver',
               help='The XenAPI VIF driver using XenServer Network APIs.'),
    cfg.BoolOpt('xenapi_generate_swap',
                default=False,
                help='Whether to generate swap '
                     '(False means fetching it from OVA)'),
    cfg.StrOpt('image_activation_file',
                default=None,
                help=_('JSON file containing image activation configuration')),

    ]

FLAGS = flags.FLAGS
FLAGS.register_opts(xenapi_vmops_opts)

flags.DECLARE('vncserver_proxyclient_address', 'nova.vnc')

RESIZE_TOTAL_STEPS = 5

DEVICE_ROOT = '0'
DEVICE_RESCUE = '1'
DEVICE_SWAP = '2'
DEVICE_EPHEMERAL = '3'
DEVICE_CD = '4'


class RaxImageActivationConfig(object):
    """Manage RAX image license activation config state """
    def __init__(self):
        self._cache = {}

        if FLAGS.image_activation_file:
            self._file_path = FLAGS.find_file(FLAGS.image_activation_file)
            self.reload()

    def reload(self):
        """(Re)load config from JSON file
        The file is a dict mapping each activation profile idsto
        a configuration value.
        E.x. file:
        {
            "1-2-3-4-5": "useful_config_value"
        }
        """

        def _reload(data):
            self._config = jsonutils.loads(data)

        utils.read_cached_file(self._file_path, self._cache,
                               reload_func=_reload)

    def get(self, image_id):
        """Get config values for the given image id """

        if not FLAGS.image_activation_file:
            return None

        self.reload()
        return self._config.get(image_id)


def cmp_version(a, b):
    """Compare two version strings (eg 0.0.1.10 > 0.0.1.9)"""
    a = a.split('.')
    b = b.split('.')

    # Compare each individual portion of both version strings
    for va, vb in zip(a, b):
        ret = int(va) - int(vb)
        if ret:
            return ret

    # Fallback to comparing length last
    return len(a) - len(b)


def make_step_decorator(context, instance):
    """Factory to create a decorator that records instance progress as a series
    of discrete steps.

    Each time the decorator is invoked we bump the total-step-count, so after::

        @step
        def step1():
            ...

        @step
        def step2():
            ...

    we have a total-step-count of 2.

    Each time the step-function (not the step-decorator!) is invoked, we bump
    the current-step-count by 1, so after::

        step1()

    the current-step-count would be 1 giving a progress of ``1 / 2 *
    100`` or 50%.
    """
    step_info = dict(total=0, current=0)

    def bump_progress():
        step_info['current'] += 1
        progress = round(float(step_info['current']) /
                         step_info['total'] * 100)
        LOG.debug(_("Updating progress to %(progress)d"), locals(),
                  instance=instance)
        db.instance_update(context, instance['uuid'], {'progress': progress})

    def step_decorator(f):
        step_info['total'] += 1

        @functools.wraps(f)
        def inner(*args, **kwargs):
            rv = f(*args, **kwargs)
            bump_progress()
            return rv

        return inner

    return step_decorator


class VMOps(object):
    """
    Management class for VM-related tasks
    """
    def __init__(self, session):
        self.XenAPI = session.get_imported_xenapi()
        self.compute_api = compute.API()
        self._session = session
        self.poll_rescue_last_ran = None
        if FLAGS.firewall_driver not in firewall.drivers:
            FLAGS.set_default('firewall_driver', firewall.drivers[0])
        fw_class = importutils.import_class(FLAGS.firewall_driver)
        self.firewall_driver = fw_class(xenapi_session=self._session)
        vif_impl = importutils.import_class(FLAGS.xenapi_vif_driver)
        self.vif_driver = vif_impl(xenapi_session=self._session)
        # configs for image license activation:
        self._rax_image_activation_config = RaxImageActivationConfig()

    def list_instances(self):
        """List VM instances."""
        # TODO(justinsb): Should we just always use the details method?
        #  Seems to be the same number of API calls..
        name_labels = []
        for vm_ref, vm_rec in vm_utils.list_vms(self._session):
            name_labels.append(vm_rec["name_label"])

        return name_labels

    def list_instances_detail(self):
        """List VM instances, returning InstanceInfo objects."""
        details = []
        for vm_ref, vm_rec in vm_utils.list_vms(self._session):
            name = vm_rec["name_label"]

            # TODO(justinsb): This a roundabout way to map the state
            openstack_format = vm_utils.compile_info(vm_rec)
            state = openstack_format['state']

            instance_info = driver.InstanceInfo(name, state)
            details.append(instance_info)

        return details

    def confirm_migration(self, migration, instance, network_info):
        name_label = self._get_orig_vm_name_label(instance)
        vm_ref = vm_utils.lookup(self._session, name_label)
        return self._destroy(instance, vm_ref, network_info)

    def finish_revert_migration(self, instance):
        # NOTE(sirp): the original vm was suffixed with '-orig'; find it using
        # the old suffix, remove the suffix, then power it back on.
        name_label = self._get_orig_vm_name_label(instance)
        vm_ref = vm_utils.lookup(self._session, name_label)

        # Remove the '-orig' suffix (which was added in case the resized VM
        # ends up on the source host, common during testing)
        name_label = instance.name
        vm_utils.set_vm_name_label(self._session, vm_ref, name_label)

        self._start(instance, vm_ref)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance):
        root_vdi = self._move_disks(instance, disk_info)

        if resize_instance:
            self._resize_instance(instance, root_vdi)

        vm_ref = self._create_vm(context, instance,
                                 {'root': root_vdi},
                                 network_info, image_meta)

        # 5. Start VM
        self._start(instance, vm_ref=vm_ref)
        self._update_instance_progress(context, instance,
                                       step=5,
                                       total_steps=RESIZE_TOTAL_STEPS)

    def _start(self, instance, vm_ref=None):
        """Power on a VM instance"""
        vm_ref = vm_ref or self._get_vm_opaque_ref(instance)
        LOG.debug(_("Starting instance"), instance=instance)
        self._session.call_xenapi('VM.start_on', vm_ref,
                                  self._session.get_xenapi_host(),
                                  False, False)

    def _create_disks(self, context, instance, image_meta):
        disk_image_type = vm_utils.determine_disk_image_type(image_meta)
        vdis = vm_utils.create_image(context, self._session,
                                     instance, instance.image_ref,
                                     disk_image_type)

        # Just get the VDI ref once
        for vdi in vdis.itervalues():
            vdi['ref'] = self._session.call_xenapi('VDI.get_by_uuid',
                                                   vdi['uuid'])

        root_vdi = vdis.get('root')
        if root_vdi:
            self._resize_instance(instance, root_vdi)

        return vdis

    def spawn(self, context, instance, image_meta, network_info):
        step = make_step_decorator(context, instance)

        @step
        def vanity_step(undo_mgr):
            # NOTE(sirp): _create_disk will potentially take a *very* long
            # time to complete since it has to fetch the image over the
            # network and images can be several gigs in size. To avoid
            # progress remaining at 0% for too long, which will appear to be
            # an error, we insert a "vanity" step to bump the progress up one
            # notch above 0.
            pass

        @step
        def create_disks_step(undo_mgr):
            vdis = self._create_disks(context, instance, image_meta)

            def undo_create_disks():
                self._safe_destroy_vdis([vdi['ref'] for vdi in vdis.values()])

            undo_mgr.undo_with(undo_create_disks)
            return vdis

        @step
        def create_kernel_ramdisk_step(undo_mgr):
            kernel_file = None
            ramdisk_file = None

            if instance.kernel_id:
                vdis = vm_utils.create_kernel_image(context, self._session,
                        instance, instance.kernel_id, instance.user_id,
                        instance.project_id, vm_utils.ImageType.KERNEL)
                kernel_file = vdis['kernel'].get('file')

            if instance.ramdisk_id:
                vdis = vm_utils.create_kernel_image(context, self._session,
                        instance, instance.ramdisk_id, instance.user_id,
                        instance.project_id, vm_utils.ImageType.RAMDISK)
                ramdisk_file = vdis['ramdisk'].get('file')

            def undo_create_kernel_ramdisk():
                if kernel_file or ramdisk_file:
                    LOG.debug(_("Removing kernel/ramdisk files from dom0"),
                              instance=instance)
                    self._destroy_kernel_ramdisk_plugin_call(kernel_file,
                                                             ramdisk_file)
            undo_mgr.undo_with(undo_create_kernel_ramdisk)
            return kernel_file, ramdisk_file

        @step
        def create_vm_step(undo_mgr, vdis, kernel_file, ramdisk_file):
            vm_ref = self._create_vm(context, instance, vdis, network_info,
                                     image_meta, kernel_file=kernel_file,
                                     ramdisk_file=ramdisk_file)

            def undo_create_vm():
                self._destroy(instance, vm_ref, network_info)

            undo_mgr.undo_with(undo_create_vm)
            return vm_ref

        @step
        def prepare_security_group_filters_step(undo_mgr):
            try:
                self.firewall_driver.setup_basic_filtering(
                        instance, network_info)
            except NotImplementedError:
                # NOTE(salvatore-orlando): setup_basic_filtering might be
                # empty or not implemented at all, as basic filter could
                # be implemented with VIF rules created by xapi plugin
                pass

            self.firewall_driver.prepare_instance_filter(instance,
                                                         network_info)

        @step
        def boot_instance_step(undo_mgr, vm_ref):
            self._boot_new_instance(instance, vm_ref, image_meta)

        @step
        def apply_security_group_filters_step(undo_mgr):
            self.firewall_driver.apply_instance_filter(instance, network_info)

        undo_mgr = utils.UndoManager()
        try:
            vanity_step(undo_mgr)

            vdis = create_disks_step(undo_mgr)
            kernel_file, ramdisk_file = create_kernel_ramdisk_step(undo_mgr)

            vm_ref = create_vm_step(undo_mgr, vdis, kernel_file, ramdisk_file)
            prepare_security_group_filters_step(undo_mgr)

            boot_instance_step(undo_mgr, vm_ref)

            apply_security_group_filters_step(undo_mgr)
        except Exception:
            msg = _("Failed to spawn, rolling back")
            undo_mgr.rollback_and_reraise(msg=msg, instance=instance)

    def _generate_hostname(self, instance):
        """Generate the instance's hostname."""
        hostname = instance["hostname"]
        if getattr(instance, "_rescue", False):
            hostname = "RESCUE-%s" % hostname

        return hostname

    def _create_vm(self, context, instance, vdis, network_info, image_meta,
                   kernel_file=None, ramdisk_file=None):
        """Create VM instance."""
        instance_name = instance.name
        vm_ref = vm_utils.lookup(self._session, instance_name)
        if vm_ref is not None:
            raise exception.InstanceExists(name=instance_name)

        # Ensure enough free memory is available
        if not vm_utils.ensure_free_mem(self._session, instance):
            raise exception.InsufficientFreeMemory(uuid=instance.uuid)

        disk_image_type = vm_utils.determine_disk_image_type(image_meta)

        vm_mode = instance.vm_mode and instance.vm_mode.lower()
        if vm_mode == 'pv':
            use_pv_kernel = True
        elif vm_mode in ('hv', 'hvm'):
            use_pv_kernel = False
            vm_mode = 'hvm'  # Normalize
        else:
            use_pv_kernel = vm_utils.determine_is_pv(self._session,
                    vdis['root']['ref'], disk_image_type, instance.os_type)
            vm_mode = use_pv_kernel and 'pv' or 'hvm'

        if instance.vm_mode != vm_mode:
            # Update database with normalized (or determined) value
            db.instance_update(nova_context.get_admin_context(),
                               instance['uuid'], {'vm_mode': vm_mode})

        vm_ref = vm_utils.create_vm(
            self._session, instance, kernel_file, ramdisk_file,
            use_pv_kernel)

        # Add disks to VM
        self._attach_disks(instance, disk_image_type, vm_ref, vdis)

        # Alter the image before VM start for network injection.
        if FLAGS.flat_injected:
            vm_utils.preconfigure_instance(self._session, instance,
                                           vdis['root']['ref'], network_info)

        self._create_vifs(vm_ref, instance, network_info)
        self.inject_network_info(instance, network_info, vm_ref)

        hostname = self._generate_hostname(instance)
        self.inject_hostname(instance, vm_ref, hostname)

        return vm_ref

    def _attach_disks(self, instance, disk_image_type, vm_ref, vdis):
        ctx = nova_context.get_admin_context()

        # DISK_ISO needs two VBDs: the ISO disk and a blank RW disk
        if disk_image_type == vm_utils.ImageType.DISK_ISO:
            LOG.debug(_("Detected ISO image type, creating blank VM "
                        "for install"), instance=instance)

            cd_vdi = vdis.pop('root')
            root_vdi = vm_utils.fetch_blank_disk(self._session,
                                                 instance.instance_type_id)
            vdis['root'] = root_vdi

            vm_utils.create_vbd(self._session, vm_ref, root_vdi['ref'],
                                DEVICE_ROOT, bootable=False)

            vm_utils.create_vbd(self._session, vm_ref, cd_vdi['ref'],
                                DEVICE_CD, vbd_type='CD', bootable=True)
        else:
            root_vdi = vdis['root']

            if instance.auto_disk_config:
                LOG.debug(_("Auto configuring disk, attempting to "
                            "resize partition..."), instance=instance)
                instance_type = db.instance_type_get(ctx,
                        instance.instance_type_id)
                vm_utils.auto_configure_disk(self._session,
                                             root_vdi['ref'],
                                             instance_type['root_gb'])

            vm_utils.create_vbd(self._session, vm_ref, root_vdi['ref'],
                                DEVICE_ROOT, bootable=True)

        # Attach (optional) swap disk
        swap_vdi = vdis.get('swap')

        instance_type = db.instance_type_get(ctx, instance.instance_type_id)
        swap_mb = instance_type['swap']
        generate_swap = swap_mb and FLAGS.xenapi_generate_swap
        if generate_swap:
            vm_utils.generate_swap(self._session, instance, vm_ref,
                                   DEVICE_SWAP, swap_mb)

            if swap_vdi:
                # We won't be using packaged swap VDI, so destroy it
                vm_utils.destroy_vdi(self._session, swap_vdi['ref'])
        elif swap_vdi:
            # Attach packaged swap VDI to VM
            vm_utils.create_vbd(self._session, vm_ref, swap_vdi['ref'],
                                DEVICE_SWAP, bootable=False)

        # Attach (optional) ephemeral disk
        ephemeral_gb = instance_type['ephemeral_gb']
        if ephemeral_gb:
            vm_utils.generate_ephemeral(self._session, instance, vm_ref,
                                        DEVICE_EPHEMERAL, ephemeral_gb)

    def _boot_new_instance(self, instance, vm_ref, image_meta):
        """Boot a new instance and configure it."""
        LOG.debug(_('Starting VM'), instance=instance)
        self._start(instance, vm_ref)

        ctx = nova_context.get_admin_context()
        agent_build = db.agent_build_get_by_triple(ctx, 'xen',
                              instance.os_type, instance.architecture)
        if agent_build:
            LOG.info(_('Latest agent build for %(hypervisor)s/%(os)s'
                       '/%(architecture)s is %(version)s') % agent_build)
        else:
            LOG.info(_('No agent build found for %(hypervisor)s/%(os)s'
                       '/%(architecture)s') % {
                        'hypervisor': 'xen',
                        'os': instance.os_type,
                        'architecture': instance.architecture})

        # Wait for boot to finish
        LOG.debug(_('Waiting for instance state to become running'),
                  instance=instance)
        expiration = time.time() + FLAGS.xenapi_running_timeout
        while time.time() < expiration:
            state = self.get_info(instance)['state']
            if state == power_state.RUNNING:
                break

            greenthread.sleep(0.5)

        # Update agent, if necessary
        # This also waits until the agent starts
        LOG.debug(_("Querying agent version"), instance=instance)
        version = self._get_agent_version(instance)
        if version:
            LOG.info(_('Instance agent version: %s'), version,
                     instance=instance)

        if (version and agent_build and
            cmp_version(version, agent_build['version']) < 0):
            LOG.info(_('Updating Agent to %s'), agent_build['version'],
                     instance=instance)
            self._agent_update(instance, agent_build['url'],
                               agent_build['md5hash'])

        # if the guest agent is not available, configure the
        # instance, but skip the admin password configuration
        no_agent = version is None

        # Inject files, if necessary
        injected_files = instance.injected_files
        if injected_files:
            # Check if this is a JSON-encoded string and convert if needed.
            if isinstance(injected_files, basestring):
                try:
                    injected_files = jsonutils.loads(injected_files)
                except ValueError:
                    LOG.exception(_("Invalid value for injected_files: %r"),
                                  injected_files, instance=instance)
                    injected_files = []
            # Inject any files, if specified
            for path, contents in instance.injected_files:
                LOG.debug(_("Injecting file path: '%s'") % path,
                          instance=instance)
                self.inject_file(instance, path, contents)

        admin_password = instance.admin_pass
        # Set admin password, if necessary
        if admin_password and not no_agent:
            LOG.debug(_("Setting admin password"), instance=instance)
            self.set_admin_password(instance, admin_password)

        # Reset network config
        LOG.debug(_("Resetting network"), instance=instance)
        self.reset_network(instance, vm_ref)

        # Set VCPU weight
        inst_type = db.instance_type_get(ctx, instance.instance_type_id)
        vcpu_weight = inst_type['vcpu_weight']
        if vcpu_weight is not None:
            LOG.debug(_("Setting VCPU weight"), instance=instance)
            self._session.call_xenapi('VM.add_to_VCPUs_params', vm_ref,
                                      'weight', str(vcpu_weight))

        # Activate OS (if necessary)
        profile = image_meta.get('properties', {}).\
                             get('rax_activation_profile')
        if profile:
            LOG.debug(_("RAX Activation Profile: %r"), profile,
                      instance=instance)

            # get matching activation config for this profile:
            config = self._rax_image_activation_config.get(profile)
            if config:
                self.activate_instance(instance, config)

    def _get_vm_opaque_ref(self, instance):
        vm_ref = vm_utils.lookup(self._session, instance['name'])
        if vm_ref is None:
            raise exception.NotFound(_('Could not find VM with name %s') %
                                     instance['name'])
        return vm_ref

    def _acquire_bootlock(self, vm):
        """Prevent an instance from booting."""
        self._session.call_xenapi(
            "VM.set_blocked_operations",
            vm,
            {"start": ""})

    def _release_bootlock(self, vm):
        """Allow an instance to boot."""
        self._session.call_xenapi(
            "VM.remove_from_blocked_operations",
            vm,
            "start")

    def snapshot(self, context, instance, image_id):
        """Create snapshot from a running VM instance.

        :param context: request context
        :param instance: instance to be snapshotted
        :param image_id: id of image to upload to

        Steps involved in a XenServer snapshot:

        1. XAPI-Snapshot: Snapshotting the instance using XenAPI. This
           creates: Snapshot (Template) VM, Snapshot VBD, Snapshot VDI,
           Snapshot VHD

        2. Wait-for-coalesce: The Snapshot VDI and Instance VDI both point to
           a 'base-copy' VDI.  The base_copy is immutable and may be chained
           with other base_copies.  If chained, the base_copies
           coalesce together, so, we must wait for this coalescing to occur to
           get a stable representation of the data on disk.

        3. Push-to-glance: Once coalesced, we call a plugin on the XenServer
           that will bundle the VHDs together and then push the bundle into
           Glance.

        """
        template_vm_ref = None
        try:
            _snapshot_info = self._create_snapshot(instance)
            template_vm_ref, template_vdi_uuids = _snapshot_info
            # call plugin to ship snapshot off to glance
            vm_utils.upload_image(context,
                    self._session, instance, template_vdi_uuids, image_id)
        finally:
            if template_vm_ref:
                self._destroy(instance, template_vm_ref,
                              destroy_kernel_ramdisk=False)

        LOG.debug(_("Finished snapshot and upload for VM"),
                  instance=instance)

    def _create_snapshot(self, instance):
        #TODO(sirp): Add quiesce and VSS locking support when Windows support
        # is added

        LOG.debug(_("Starting snapshot for VM"), instance=instance)
        vm_ref = self._get_vm_opaque_ref(instance)

        label = "%s-snapshot" % instance.name
        try:
            template_vm_ref, template_vdi_uuids = vm_utils.create_snapshot(
                    self._session, instance, vm_ref, label)
            return template_vm_ref, template_vdi_uuids
        except self.XenAPI.Failure, exc:
            LOG.error(_("Unable to Snapshot instance: %(exc)s"), locals(),
                      instance=instance)
            raise

    def _migrate_vhd(self, instance, vdi_uuid, dest, sr_path):
        instance_uuid = instance['uuid']
        params = {'host': dest,
                  'vdi_uuid': vdi_uuid,
                  'instance_uuid': instance_uuid,
                  'sr_path': sr_path}

        try:
            _params = {'params': pickle.dumps(params)}
            self._session.call_plugin('migration', 'transfer_vhd',
                                      _params)
        except self.XenAPI.Failure:
            msg = _("Failed to transfer vhd to new host")
            raise exception.MigrationError(reason=msg)

    def _get_orig_vm_name_label(self, instance):
        return instance.name + '-orig'

    def _update_instance_progress(self, context, instance, step, total_steps):
        """Update instance progress percent to reflect current step number
        """
        # FIXME(sirp): for now we're taking a KISS approach to instance
        # progress:
        # Divide the action's workflow into discrete steps and "bump" the
        # instance's progress field as each step is completed.
        #
        # For a first cut this should be fine, however, for large VM images,
        # the _create_disks step begins to dominate the equation. A
        # better approximation would use the percentage of the VM image that
        # has been streamed to the destination host.
        progress = round(float(step) / total_steps * 100)
        LOG.debug(_("Updating progress to %(progress)d"), locals(),
                  instance=instance)
        db.instance_update(context, instance['uuid'], {'progress': progress})

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   instance_type):
        """Copies a VHD from one host machine to another, possibly
        resizing filesystem before hand.

        :param instance: the instance that owns the VHD in question.
        :param dest: the destination host machine.
        :param disk_type: values are 'primary' or 'cow'.

        """
        # 0. Zero out the progress to begin
        self._update_instance_progress(context, instance,
                                       step=0,
                                       total_steps=RESIZE_TOTAL_STEPS)

        vm_ref = self._get_vm_opaque_ref(instance)

        # The primary VDI becomes the COW after the snapshot, and we can
        # identify it via the VBD. The base copy is the parent_uuid returned
        # from the snapshot creation

        base_copy_uuid = cow_uuid = None
        template_vdi_uuids = template_vm_ref = None
        try:
            # 1. Create Snapshot
            _snapshot_info = self._create_snapshot(instance)
            template_vm_ref, template_vdi_uuids = _snapshot_info
            self._update_instance_progress(context, instance,
                                           step=1,
                                           total_steps=RESIZE_TOTAL_STEPS)

            base_copy_uuid = template_vdi_uuids['image']
            _vdi_info = vm_utils.get_vdi_for_vm_safely(self._session, vm_ref)
            vdi_ref, vm_vdi_rec = _vdi_info
            cow_uuid = vm_vdi_rec['uuid']

            sr_path = vm_utils.get_sr_path(self._session)

            if (instance['auto_disk_config'] and
                instance['root_gb'] > instance_type['root_gb']):
                # Resizing disk storage down
                old_gb = instance['root_gb']
                new_gb = instance_type['root_gb']

                LOG.debug(_("Resizing down VDI %(cow_uuid)s from "
                            "%(old_gb)dGB to %(new_gb)dGB"), locals(),
                          instance=instance)

                # 2. Power down the instance before resizing
                self._shutdown(instance, vm_ref, hard=False)
                self._update_instance_progress(context, instance,
                                               step=2,
                                               total_steps=RESIZE_TOTAL_STEPS)

                # 3. Copy VDI, resize partition and filesystem, forget VDI,
                # truncate VHD
                new_ref, new_uuid = vm_utils.resize_disk(self._session,
                                                         instance,
                                                         vdi_ref,
                                                         instance_type)
                self._update_instance_progress(context, instance,
                                               step=3,
                                               total_steps=RESIZE_TOTAL_STEPS)

                # 4. Transfer the new VHD
                self._migrate_vhd(instance, new_uuid, dest, sr_path)
                self._update_instance_progress(context, instance,
                                               step=4,
                                               total_steps=RESIZE_TOTAL_STEPS)

                # Clean up VDI now that it's been copied
                vm_utils.destroy_vdi(self._session, new_ref)

                vdis = {'base_copy': new_uuid}
            else:
                # Resizing disk storage up, will be handled on destination

                # As an optimization, we transfer the base VDI first,
                # then shut down the VM, followed by transfering the COW
                # VDI.

                # 2. Transfer the base copy
                self._migrate_vhd(instance, base_copy_uuid, dest, sr_path)
                self._update_instance_progress(context, instance,
                                               step=2,
                                               total_steps=RESIZE_TOTAL_STEPS)

                # 3. Now power down the instance
                self._shutdown(instance, vm_ref, hard=False)
                self._update_instance_progress(context, instance,
                                               step=3,
                                               total_steps=RESIZE_TOTAL_STEPS)

                # 4. Transfer the COW VHD
                self._migrate_vhd(instance, cow_uuid, dest, sr_path)
                self._update_instance_progress(context, instance,
                                               step=4,
                                               total_steps=RESIZE_TOTAL_STEPS)

                # TODO(mdietz): we could also consider renaming these to
                # something sensible so we don't need to blindly pass
                # around dictionaries
                vdis = {'base_copy': base_copy_uuid, 'cow': cow_uuid}

            # NOTE(sirp): in case we're resizing to the same host (for dev
            # purposes), apply a suffix to name-label so the two VM records
            # extant until a confirm_resize don't collide.
            name_label = self._get_orig_vm_name_label(instance)
            vm_utils.set_vm_name_label(self._session, vm_ref, name_label)
        finally:
            if template_vm_ref:
                self._destroy(instance, template_vm_ref,
                              destroy_kernel_ramdisk=False)

        return vdis

    def _move_disks(self, instance, disk_info):
        """Move and possibly link VHDs via the XAPI plugin."""
        base_copy_uuid = disk_info['base_copy']
        new_base_copy_uuid = str(uuid.uuid4())

        params = {'instance_uuid': instance['uuid'],
                  'sr_path': vm_utils.get_sr_path(self._session),
                  'old_base_copy_uuid': base_copy_uuid,
                  'new_base_copy_uuid': new_base_copy_uuid}

        if 'cow' in disk_info:
            cow_uuid = disk_info['cow']
            new_cow_uuid = str(uuid.uuid4())
            params['old_cow_uuid'] = cow_uuid
            params['new_cow_uuid'] = new_cow_uuid

            new_uuid = new_cow_uuid
        else:
            new_uuid = new_base_copy_uuid

        self._session.call_plugin('migration', 'move_vhds_into_sr',
                                  {'params': pickle.dumps(params)})

        # Now we rescan the SR so we find the VHDs
        vm_utils.scan_default_sr(self._session)

        # Set name-label so we can find if we need to clean up a failed
        # migration
        vm_utils.set_vdi_name(self._session, new_uuid, instance.name, 'root')

        new_ref = self._session.call_xenapi('VDI.get_by_uuid', new_uuid)

        return {'uuid': new_uuid, 'ref': new_ref}

    def _resize_instance(self, instance, root_vdi):
        """Resize an instances root disk."""

        new_disk_size = instance.root_gb * 1024 * 1024 * 1024
        if not new_disk_size:
            return

        # Get current size of VDI
        virtual_size = self._session.call_xenapi('VDI.get_virtual_size',
                                                 root_vdi['ref'])
        virtual_size = int(virtual_size)

        old_gb = virtual_size / (1024 * 1024 * 1024)
        new_gb = instance.root_gb

        if virtual_size < new_disk_size:
            # Resize up. Simple VDI resize will do the trick
            vdi_uuid = root_vdi['uuid']
            LOG.debug(_("Resizing up VDI %(vdi_uuid)s from %(old_gb)dGB to "
                        "%(new_gb)dGB"), locals(), instance=instance)
            if self._session.product_version[0] > 5:
                resize_func_name = 'VDI.resize'
            else:
                resize_func_name = 'VDI.resize_online'
            self._session.call_xenapi(resize_func_name, root_vdi['ref'],
                    str(new_disk_size))
            LOG.debug(_("Resize complete"), instance=instance)

    def reboot(self, instance, reboot_type):
        """Reboot VM instance."""
        # Note (salvatore-orlando): security group rules are not re-enforced
        # upon reboot, since this action on the XenAPI drivers does not
        # remove existing filters
        vm_ref = self._get_vm_opaque_ref(instance)

        if reboot_type == "HARD":
            self._session.call_xenapi('VM.hard_reboot', vm_ref)
        else:
            self._session.call_xenapi('VM.clean_reboot', vm_ref)

    def _get_agent_version(self, instance):
        """Get the version of the agent running on the VM instance."""

        # The agent can be slow to start for a variety of reasons. On Windows,
        # it will generally perform a setup process on first boot that can
        # take a couple of minutes and then reboot. On Linux, the system can
        # also take a while to boot. So we need to be more patient than
        # normal as well as watch for domid changes

        def _call():
            # Send the encrypted password
            resp = self._make_agent_call('version', instance)
            if resp['returncode'] != '0':
                LOG.error(_('Failed to query agent version: %(resp)r'),
                          locals(), instance=instance)
                return None
            # Some old versions of the Windows agent have a trailing \\r\\n
            # (ie CRLF escaped) for some reason. Strip that off.
            return resp['message'].replace('\\r\\n', '')

        vm_ref = self._get_vm_opaque_ref(instance)
        vm_rec = self._session.call_xenapi("VM.get_record", vm_ref)

        domid = vm_rec['domid']

        expiration = time.time() + FLAGS.agent_version_timeout
        while time.time() < expiration:
            ret = _call()
            if ret:
                return ret

            vm_rec = self._session.call_xenapi("VM.get_record", vm_ref)
            if vm_rec['domid'] != domid:
                newdomid = vm_rec['domid']
                LOG.info(_('domid changed from %(domid)s to %(newdomid)s'),
                         locals(), instance=instance)
                domid = vm_rec['domid']

        return None

    def _agent_update(self, instance, url, md5sum):
        """Update agent on the VM instance."""

        # Send the encrypted password
        args = {'url': url, 'md5sum': md5sum}
        resp = self._make_agent_call('agentupdate', instance, args)
        if resp['returncode'] != '0':
            LOG.error(_('Failed to update agent: %(resp)r'), locals(),
                      instance=instance)
            return None
        return resp['message']

    def set_admin_password(self, instance, new_pass):
        """Set the root/admin password on the VM instance.

        This is done via an agent running on the VM. Communication between nova
        and the agent is done via writing xenstore records. Since communication
        is done over the XenAPI RPC calls, we need to encrypt the password.
        We're using a simple Diffie-Hellman class instead of the more advanced
        one in M2Crypto for compatibility with the agent code.

        """
        # The simple Diffie-Hellman class is used to manage key exchange.
        dh = SimpleDH()
        key_init_args = {'pub': str(dh.get_public())}
        resp = self._make_agent_call('key_init', instance, key_init_args)
        # Successful return code from key_init is 'D0'
        if resp['returncode'] != 'D0':
            msg = _('Failed to exchange keys: %(resp)r') % locals()
            LOG.error(msg, instance=instance)
            raise Exception(msg)
        # Some old versions of the Windows agent have a trailing \\r\\n
        # (ie CRLF escaped) for some reason. Strip that off.
        agent_pub = int(resp['message'].replace('\\r\\n', ''))
        dh.compute_shared(agent_pub)
        # Some old versions of Linux and Windows agent expect trailing \n
        # on password to work correctly.
        enc_pass = dh.encrypt(new_pass + '\n')
        # Send the encrypted password
        password_args = {'enc_pass': enc_pass}
        resp = self._make_agent_call('password', instance, password_args)
        # Successful return code from password is '0'
        if resp['returncode'] != '0':
            msg = _('Failed to update password: %(resp)r') % locals()
            LOG.error(msg, instance=instance)
            raise Exception(msg)
        return resp['message']

    def inject_file(self, instance, path, contents):
        """Write a file to the VM instance.

        The path to which it is to be written and the contents of the file
        need to be supplied; both will be base64-encoded to prevent errors
        with non-ASCII characters being transmitted. If the agent does not
        support file injection, or the user has disabled it, a
        NotImplementedError will be raised.

        """
        # Files/paths must be base64-encoded for transmission to agent
        b64_path = base64.b64encode(path)
        b64_contents = base64.b64encode(contents)

        # Need to uniquely identify this request.
        args = {'b64_path': b64_path, 'b64_contents': b64_contents}
        # If the agent doesn't support file injection, a NotImplementedError
        # will be raised with the appropriate message.
        resp = self._make_agent_call('inject_file', instance, args)
        if resp['returncode'] != '0':
            LOG.error(_('Failed to inject file: %(resp)r'), locals(),
                      instance=instance)
            return None
        return resp['message']

    def _shutdown(self, instance, vm_ref, hard=True):
        """Shutdown an instance."""
        vm_rec = self._session.call_xenapi("VM.get_record", vm_ref)
        state = vm_utils.compile_info(vm_rec)['state']
        if state == power_state.SHUTDOWN:
            LOG.warn(_("VM already halted, skipping shutdown..."),
                     instance=instance)
            return

        LOG.debug(_("Shutting down VM"), instance=instance)
        try:
            if hard:
                self._session.call_xenapi('VM.hard_shutdown', vm_ref)
            else:
                self._session.call_xenapi('VM.clean_shutdown', vm_ref)
        except self.XenAPI.Failure, exc:
            LOG.exception(exc)

    def _find_root_vdi_ref(self, vm_ref):
        """Find and return the root vdi ref for a VM."""
        if not vm_ref:
            return None

        vbd_refs = self._session.call_xenapi("VM.get_VBDs", vm_ref)

        for vbd_uuid in vbd_refs:
            vbd = self._session.call_xenapi("VBD.get_record", vbd_uuid)
            if vbd["userdevice"] == DEVICE_ROOT:
                return vbd["VDI"]

        raise exception.NotFound(_("Unable to find root VBD/VDI for VM"))

    def _safe_destroy_vdis(self, vdi_refs):
        """Destroys the requested VDIs, logging any StorageError exceptions."""
        for vdi_ref in vdi_refs:
            try:
                vm_utils.destroy_vdi(self._session, vdi_ref)
            except volume_utils.StorageError as exc:
                LOG.error(exc)

    def _destroy_kernel_ramdisk_plugin_call(self, kernel, ramdisk):
        args = {}
        if kernel:
            args['kernel-file'] = kernel
        if ramdisk:
            args['ramdisk-file'] = ramdisk
        self._session.call_plugin('glance', 'remove_kernel_ramdisk', args)

    def _destroy_kernel_ramdisk(self, instance, vm_ref):
        """Three situations can occur:

            1. We have neither a ramdisk nor a kernel, in which case we are a
               RAW image and can omit this step

            2. We have one or the other, in which case, we should flag as an
               error

            3. We have both, in which case we safely remove both the kernel
               and the ramdisk.

        """
        instance_uuid = instance['uuid']
        if not instance.kernel_id and not instance.ramdisk_id:
            # 1. No kernel or ramdisk
            LOG.debug(_("Using RAW or VHD, skipping kernel and ramdisk "
                        "deletion"), instance=instance)
            return

        if not (instance.kernel_id and instance.ramdisk_id):
            # 2. We only have kernel xor ramdisk
            raise exception.InstanceUnacceptable(instance_id=instance_uuid,
               reason=_("instance has a kernel or ramdisk but not both"))

        # 3. We have both kernel and ramdisk
        (kernel, ramdisk) = vm_utils.lookup_kernel_ramdisk(self._session,
                                                           vm_ref)

        self._destroy_kernel_ramdisk_plugin_call(kernel, ramdisk)
        LOG.debug(_("kernel/ramdisk files removed"), instance=instance)

    def _destroy_vm(self, instance, vm_ref):
        """Destroys a VM record."""
        try:
            self._session.call_xenapi('VM.destroy', vm_ref)
        except self.XenAPI.Failure, exc:
            LOG.exception(exc)
            return

        LOG.debug(_("VM destroyed"), instance=instance)

    def _destroy_rescue_instance(self, rescue_vm_ref, original_vm_ref):
        """Destroy a rescue instance."""
        # Shutdown Rescue VM
        vm_rec = self._session.call_xenapi("VM.get_record", rescue_vm_ref)
        state = vm_utils.compile_info(vm_rec)['state']
        if state != power_state.SHUTDOWN:
            self._session.call_xenapi("VM.hard_shutdown", rescue_vm_ref)

        # Destroy Rescue VDIs
        vdi_refs = vm_utils.lookup_vm_vdis(self._session, rescue_vm_ref)
        root_vdi_ref = self._find_root_vdi_ref(original_vm_ref)
        vdi_refs = [vdi_ref for vdi_ref in vdi_refs if vdi_ref != root_vdi_ref]
        self._safe_destroy_vdis(vdi_refs)

        # Destroy Rescue VM
        self._session.call_xenapi("VM.destroy", rescue_vm_ref)

    def destroy(self, instance, network_info):
        """Destroy VM instance.

        This is the method exposed by xenapi_conn.destroy(). The rest of the
        destroy_* methods are internal.

        """
        LOG.info(_("Destroying VM"), instance=instance)

        # We don't use _get_vm_opaque_ref because the instance may
        # truly not exist because of a failure during build. A valid
        # vm_ref is checked correctly where necessary.
        vm_ref = vm_utils.lookup(self._session, instance['name'])

        rescue_vm_ref = vm_utils.lookup(self._session,
                                        "%s-rescue" % instance.name)
        if rescue_vm_ref:
            self._destroy_rescue_instance(rescue_vm_ref, vm_ref)

        return self._destroy(instance, vm_ref, network_info)

    def _destroy(self, instance, vm_ref, network_info=None,
                 destroy_kernel_ramdisk=True):
        """Destroys VM instance by performing:

            1. A shutdown
            2. Destroying associated VDIs.
            3. Destroying kernel and ramdisk files (if necessary).
            4. Destroying that actual VM record.

        """
        if vm_ref is None:
            LOG.warning(_("VM is not present, skipping destroy..."),
                        instance=instance)
            return
        is_snapshot = vm_utils.is_snapshot(self._session, vm_ref)
        self._shutdown(instance, vm_ref)

        # Destroy VDIs
        vdi_refs = vm_utils.lookup_vm_vdis(self._session, vm_ref)
        self._safe_destroy_vdis(vdi_refs)

        if destroy_kernel_ramdisk:
            self._destroy_kernel_ramdisk(instance, vm_ref)

        self._destroy_vm(instance, vm_ref)
        self.unplug_vifs(instance, network_info)
        # Remove security groups filters for instance
        # Unless the vm is a snapshot
        if not is_snapshot:
            self.firewall_driver.unfilter_instance(instance,
                                                   network_info=network_info)

    def pause(self, instance):
        """Pause VM instance."""
        vm_ref = self._get_vm_opaque_ref(instance)
        self._session.call_xenapi('VM.pause', vm_ref)

    def unpause(self, instance):
        """Unpause VM instance."""
        vm_ref = self._get_vm_opaque_ref(instance)
        self._session.call_xenapi('VM.unpause', vm_ref)

    def suspend(self, instance):
        """Suspend the specified instance."""
        vm_ref = self._get_vm_opaque_ref(instance)
        self._session.call_xenapi('VM.suspend', vm_ref)

    def resume(self, instance):
        """Resume the specified instance."""
        vm_ref = self._get_vm_opaque_ref(instance)
        self._session.call_xenapi('VM.resume', vm_ref, False, True)

    def rescue(self, context, instance, network_info, image_meta):
        """Rescue the specified instance.

            - shutdown the instance VM.
            - set 'bootlock' to prevent the instance from starting in rescue.
            - spawn a rescue VM (the vm name-label will be instance-N-rescue).

        """
        rescue_vm_ref = vm_utils.lookup(self._session,
                                        "%s-rescue" % instance.name)
        if rescue_vm_ref:
            raise RuntimeError(_("Instance is already in Rescue Mode: %s")
                               % instance.name)

        vm_ref = self._get_vm_opaque_ref(instance)
        self._shutdown(instance, vm_ref)
        self._acquire_bootlock(vm_ref)
        instance._rescue = True
        self.spawn(context, instance, image_meta, network_info)
        # instance.name now has -rescue appended because of magic
        rescue_vm_ref = vm_utils.lookup(self._session, instance.name)
        vdi_ref = self._find_root_vdi_ref(vm_ref)

        rescue_vbd_ref = vm_utils.create_vbd(self._session, rescue_vm_ref,
                                             vdi_ref, DEVICE_RESCUE,
                                             bootable=False)
        self._session.call_xenapi('VBD.plug', rescue_vbd_ref)

    def unrescue(self, instance):
        """Unrescue the specified instance.

            - unplug the instance VM's disk from the rescue VM.
            - teardown the rescue VM.
            - release the bootlock to allow the instance VM to start.

        """
        rescue_vm_ref = vm_utils.lookup(self._session,
                                        "%s-rescue" % instance.name)
        if not rescue_vm_ref:
            raise exception.InstanceNotInRescueMode(instance_id=instance.uuid)

        original_vm_ref = self._get_vm_opaque_ref(instance)
        instance._rescue = False

        self._destroy_rescue_instance(rescue_vm_ref, original_vm_ref)
        self._release_bootlock(original_vm_ref)
        self._start(instance, original_vm_ref)

    def power_off(self, instance):
        """Power off the specified instance."""
        vm_ref = self._get_vm_opaque_ref(instance)
        self._shutdown(instance, vm_ref, hard=True)

    def power_on(self, instance):
        """Power on the specified instance."""
        vm_ref = self._get_vm_opaque_ref(instance)
        self._start(instance, vm_ref)

    def _cancel_stale_tasks(self, timeout, task):
        """Cancel the given tasks that are older than the given timeout."""
        task_refs = self._session.call_xenapi("task.get_by_name_label", task)
        for task_ref in task_refs:
            task_rec = self._session.call_xenapi("task.get_record", task_ref)
            task_created = timeutils.parse_strtime(task_rec["created"].value,
                                                   "%Y%m%dT%H:%M:%SZ")

            if timeutils.is_older_than(task_created, timeout):
                self._session.call_xenapi("task.cancel", task_ref)

    def poll_rebooting_instances(self, timeout):
        """Look for expirable rebooting instances.

            - issue a "hard" reboot to any instance that has been stuck in a
              reboot state for >= the given timeout
        """
        # NOTE(jk0): All existing clean_reboot tasks must be cancelled before
        # we can kick off the hard_reboot tasks.
        self._cancel_stale_tasks(timeout, 'VM.clean_reboot')

        ctxt = nova_context.get_admin_context()
        instances = db.instance_get_all_hung_in_rebooting(ctxt, timeout)

        instances_info = dict(instance_count=len(instances),
                timeout=timeout)

        if instances_info["instance_count"] > 0:
            LOG.info(_("Found %(instance_count)d hung reboots "
                       "older than %(timeout)d seconds") % instances_info)

        for instance in instances:
            LOG.info(_("Automatically hard rebooting"), instance=instance)
            self.compute_api.reboot(ctxt, instance, "HARD")

    def poll_rescued_instances(self, timeout):
        """Look for expirable rescued instances.

            - forcibly exit rescue mode for any instances that have been
              in rescue mode for >= the provided timeout

        """
        last_ran = self.poll_rescue_last_ran
        if not last_ran:
            # We need a base time to start tracking.
            self.poll_rescue_last_ran = timeutils.utcnow()
            return

        if not timeutils.is_older_than(last_ran, timeout):
            # Do not run. Let's bail.
            return

        # Update the time tracker and proceed.
        self.poll_rescue_last_ran = timeutils.utcnow()

        rescue_vms = []
        for instance in self.list_instances():
            if instance.endswith("-rescue"):
                rescue_vms.append(dict(name=instance,
                                       vm_ref=vm_utils.lookup(self._session,
                                                              instance)))

        for vm in rescue_vms:
            rescue_vm_ref = vm["vm_ref"]

            original_name = vm["name"].split("-rescue", 1)[0]
            original_vm_ref = vm_utils.lookup(self._session, original_name)

            self._destroy_rescue_instance(rescue_vm_ref, original_vm_ref)

            self._release_bootlock(original_vm_ref)
            self._session.call_xenapi("VM.start", original_vm_ref, False,
                                      False)

    def get_info(self, instance):
        """Return data about VM instance."""
        vm_ref = self._get_vm_opaque_ref(instance)
        vm_rec = self._session.call_xenapi("VM.get_record", vm_ref)
        return vm_utils.compile_info(vm_rec)

    def get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        vm_ref = self._get_vm_opaque_ref(instance)
        vm_rec = self._session.call_xenapi("VM.get_record", vm_ref)
        return vm_utils.compile_diagnostics(vm_rec)

    def get_all_bw_usage(self, start_time, stop_time=None):
        """Return bandwidth usage info for each interface on each
           running VM"""
        try:
            metrics = vm_utils.compile_metrics(start_time, stop_time)
        except exception.CouldNotFetchMetrics:
            LOG.exception(_("Could not get bandwidth info."))
            return {}
        bw = {}
        for uuid, data in metrics.iteritems():
            vm_ref = self._session.call_xenapi("VM.get_by_uuid", uuid)
            vm_rec = self._session.call_xenapi("VM.get_record", vm_ref)
            vif_map = {}
            for vif in [self._session.call_xenapi("VIF.get_record", vrec)
                        for vrec in vm_rec['VIFs']]:
                vif_map[vif['device']] = vif['MAC']
            name = vm_rec['name_label']
            if vm_rec["is_a_template"] or vm_rec["is_control_domain"]:
                continue
            vifs_bw = bw.setdefault(name, {})
            for key, val in data.iteritems():
                if key.startswith('vif_'):
                    vname = key.split('_')[1]
                    vif_bw = vifs_bw.setdefault(vif_map[vname], {})
                    if key.endswith('tx'):
                        vif_bw['bw_out'] = int(val)
                    if key.endswith('rx'):
                        vif_bw['bw_in'] = int(val)
        return bw

    def get_console_output(self, instance):
        """Return snapshot of console."""
        # TODO(armando-migliaccio): implement this to fix pylint!
        return 'FAKE CONSOLE OUTPUT of instance'

    def get_vnc_console(self, instance):
        """Return connection info for a vnc console."""
        vm_ref = self._get_vm_opaque_ref(instance)
        session_id = self._session.get_session_id()
        path = "/console?ref=%s&session_id=%s" % (str(vm_ref), session_id)

        # NOTE: XS5.6sp2+ use http over port 80 for xenapi com
        return {'host': FLAGS.vncserver_proxyclient_address, 'port': 80,
                'internal_access_path': path}

    def _vif_xenstore_data(self, vif):
        """convert a network info vif to injectable instance data"""

        def get_ip(ip):
            if not ip:
                return None
            return ip['address']

        def fixed_ip_dict(ip, subnet):
            if ip['version'] == 4:
                netmask = str(subnet.as_netaddr().netmask)
            else:
                netmask = subnet.as_netaddr()._prefixlen

            return {'ip': ip['address'],
                    'enabled': '1',
                    'netmask': netmask,
                    'gateway': get_ip(subnet['gateway'])}

        def convert_route(route):
            return {'route': str(netaddr.IPNetwork(route['cidr']).network),
                    'netmask': str(netaddr.IPNetwork(route['cidr']).netmask),
                    'gateway': get_ip(route['gateway'])}

        network = vif['network']
        v4_subnets = [subnet for subnet in network['subnets']
                             if subnet['version'] == 4]
        v6_subnets = [subnet for subnet in network['subnets']
                             if subnet['version'] == 6]

        # NOTE(tr3buchet): routes and DNS come from all subnets
        routes = [convert_route(route) for subnet in network['subnets']
                                       for route in subnet['routes']]
        dns = [get_ip(ip) for subnet in network['subnets']
                          for ip in subnet['dns']]

        info_dict = {'label': network['label'],
                     'mac': vif['address']}

        if v4_subnets:
            # NOTE(tr3buchet): gateway and broadcast from first subnet
            #                  primary IP will be from first subnet
            #                  subnets are generally unordered :(
            info_dict['gateway'] = get_ip(v4_subnets[0]['gateway'])
            info_dict['broadcast'] = str(v4_subnets[0].as_netaddr().broadcast)
            info_dict['ips'] = [fixed_ip_dict(ip, subnet)
                                for subnet in v4_subnets
                                for ip in subnet['ips']]
        if v6_subnets:
            # NOTE(tr3buchet): gateway from first subnet
            #                  primary IP will be from first subnet
            #                  subnets are generally unordered :(
            info_dict['gateway_v6'] = get_ip(v6_subnets[0]['gateway'])
            info_dict['ip6s'] = [fixed_ip_dict(ip, subnet)
                                 for subnet in v6_subnets
                                 for ip in subnet['ips']]
        if routes:
            info_dict['routes'] = routes

        if dns:
            info_dict['dns'] = dns

        return info_dict

    def inject_network_info(self, instance, network_info, vm_ref=None):
        """
        Generate the network info and make calls to place it into the
        xenstore and the xenstore param list.
        vm_ref can be passed in because it will sometimes be different than
        what vm_utils.lookup(session, instance.name) will find (ex: rescue)
        """
        vm_ref = vm_ref or self._get_vm_opaque_ref(instance)
        LOG.debug(_("Injecting network info to xenstore"), instance=instance)

        for vif in network_info:
            xs_data = self._vif_xenstore_data(vif)
            location = ('vm-data/networking/%s' %
                        vif['address'].replace(':', ''))
            self._add_to_param_xenstore(vm_ref,
                                        location,
                                        jsonutils.dumps(xs_data))
            try:
                self._write_to_xenstore(instance, location, xs_data,
                                        vm_ref=vm_ref)
            except KeyError:
                # catch KeyError for domid if instance isn't running
                pass

    def _create_vifs(self, vm_ref, instance, network_info):
        """Creates vifs for an instance."""

        LOG.debug(_("Creating vifs"), instance=instance)

        # this function raises if vm_ref is not a vm_opaque_ref
        self._session.call_xenapi("VM.get_record", vm_ref)

        for device, vif in enumerate(network_info):
            vif_rec = self.vif_driver.plug(instance, vif,
                                           vm_ref=vm_ref, device=device)
            network_ref = vif_rec['network']
            LOG.debug(_('Creating VIF for network %(network_ref)s'),
                      locals(), instance=instance)
            vif_ref = self._session.call_xenapi('VIF.create', vif_rec)
            LOG.debug(_('Created VIF %(vif_ref)s, network %(network_ref)s'),
                      locals(), instance=instance)

    def plug_vifs(self, instance, network_info):
        """Set up VIF networking on the host."""
        for device, vif in enumerate(network_info):
            self.vif_driver.plug(instance, vif, device=device)

    def unplug_vifs(self, instance, network_info):
        if network_info:
            for vif in network_info:
                self.vif_driver.unplug(instance, vif)

    def reset_network(self, instance, vm_ref=None):
        """Calls resetnetwork method in agent."""
        self._make_agent_call('resetnetwork', instance, vm_ref=vm_ref)

    def activate_instance(self, instance, config):
        """Creates uuid arg to pass to make_agent_call and calls it."""

        # Windows doesn't use a dict, but also doesn't have a list of
        # domains nor a profile name
        if isinstance(config, dict):
            if 'domains' in config:
                # Shuffle list so not all instances prefer the same server
                random.shuffle(config['domains'])
            config['profile'] = instance.name

        args = {'config': jsonutils.dumps(config)}
        self._make_agent_call('kmsactivate', instance, args)

    def inject_hostname(self, instance, vm_ref, hostname):
        """Inject the hostname of the instance into the xenstore."""
        if instance.os_type == "windows":
            # NOTE(jk0): Windows hostnames can only be <= 15 chars.
            hostname = hostname[:15]

        LOG.debug(_("Injecting hostname to xenstore"), instance=instance)
        self._add_to_param_xenstore(vm_ref, 'vm-data/hostname', hostname)

    def _write_to_xenstore(self, instance, path, value, vm_ref=None):
        """
        Writes the passed value to the xenstore record for the given VM
        at the specified location. A XenAPIPlugin.PluginError will be raised
        if any error is encountered in the write process.
        """
        return self._make_plugin_call('xenstore.py', 'write_record', instance,
                                      vm_ref=vm_ref, path=path,
                                      value=jsonutils.dumps(value))

    def _make_agent_call(self, method, instance, args=None, vm_ref=None):
        """Abstracts out the interaction with the agent xenapi plugin."""
        if args is None:
            args = {}
        args['id'] = str(uuid.uuid4())
        ret = self._make_plugin_call('agent', method, instance, vm_ref=vm_ref,
                                     **args)
        if isinstance(ret, dict):
            return ret
        try:
            return jsonutils.loads(ret)
        except TypeError:
            LOG.error(_('The agent call to %(method)s returned an invalid'
                        ' response: %(ret)r. path=%(path)s; args=%(args)r'),
                      locals(), instance=instance)
            return {'returncode': 'error',
                    'message': 'unable to deserialize response'}

    def _make_plugin_call(self, plugin, method, instance, vm_ref=None,
                          **addl_args):
        """
        Abstracts out the process of calling a method of a xenapi plugin.
        Any errors raised by the plugin will in turn raise a RuntimeError here.
        """
        vm_ref = vm_ref or self._get_vm_opaque_ref(instance)
        vm_rec = self._session.call_xenapi("VM.get_record", vm_ref)
        args = {'dom_id': vm_rec['domid']}
        args.update(addl_args)
        try:
            return self._session.call_plugin(plugin, method, args)
        except self.XenAPI.Failure, e:
            err_msg = e.details[-1].splitlines()[-1]
            if 'TIMEOUT:' in err_msg:
                LOG.error(_('TIMEOUT: The call to %(method)s timed out. '
                            'args=%(args)r'), locals(), instance=instance)
                return {'returncode': 'timeout', 'message': err_msg}
            elif 'NOT IMPLEMENTED:' in err_msg:
                LOG.error(_('NOT IMPLEMENTED: The call to %(method)s is not'
                            ' supported by the agent. args=%(args)r'),
                          locals(), instance=instance)
                return {'returncode': 'notimplemented', 'message': err_msg}
            else:
                LOG.error(_('The call to %(method)s returned an error: %(e)s. '
                            'args=%(args)r'), locals(), instance=instance)
                return {'returncode': 'error', 'message': err_msg}
            return None

    def _add_to_param_xenstore(self, vm_ref, key, val):
        """
        Takes a key/value pair and adds it to the xenstore parameter
        record for the given vm instance. If the key exists in xenstore,
        it is overwritten
        """
        self._remove_from_param_xenstore(vm_ref, key)
        self._session.call_xenapi('VM.add_to_xenstore_data', vm_ref, key, val)

    def _remove_from_param_xenstore(self, vm_ref, key):
        """
        Takes a single key and removes it from the xenstore parameter
        record data for the given VM.
        If the key doesn't exist, the request is ignored.
        """
        self._session.call_xenapi('VM.remove_from_xenstore_data', vm_ref, key)

    def refresh_security_group_rules(self, security_group_id):
        """ recreates security group rules for every instance """
        self.firewall_driver.refresh_security_group_rules(security_group_id)

    def refresh_security_group_members(self, security_group_id):
        """ recreates security group rules for every instance """
        self.firewall_driver.refresh_security_group_members(security_group_id)

    def refresh_provider_fw_rules(self):
        self.firewall_driver.refresh_provider_fw_rules()

    def unfilter_instance(self, instance_ref, network_info):
        """Removes filters for each VIF of the specified instance."""
        self.firewall_driver.unfilter_instance(instance_ref,
                                               network_info=network_info)


class SimpleDH(object):
    """
    This class wraps all the functionality needed to implement
    basic Diffie-Hellman-Merkle key exchange in Python. It features
    intelligent defaults for the prime and base numbers needed for the
    calculation, while allowing you to supply your own. It requires that
    the openssl binary be installed on the system on which this is run,
    as it uses that to handle the encryption and decryption. If openssl
    is not available, a RuntimeError will be raised.
    """
    def __init__(self):
        self._prime = 162259276829213363391578010288127
        self._base = 5
        self._public = None
        self._shared = None
        self.generate_private()

    def generate_private(self):
        self._private = int(binascii.hexlify(os.urandom(10)), 16)
        return self._private

    def get_public(self):
        self._public = self.mod_exp(self._base, self._private, self._prime)
        return self._public

    def compute_shared(self, other):
        self._shared = self.mod_exp(other, self._private, self._prime)
        return self._shared

    @staticmethod
    def mod_exp(num, exp, mod):
        """Efficient implementation of (num ** exp) % mod"""
        result = 1
        while exp > 0:
            if (exp & 1) == 1:
                result = (result * num) % mod
            exp = exp >> 1
            num = (num * num) % mod
        return result

    def _run_ssl(self, text, decrypt=False):
        cmd = ['openssl', 'aes-128-cbc', '-A', '-a', '-pass',
              'pass:%s' % self._shared, '-nosalt']
        if decrypt:
            cmd.append('-d')
        out, err = utils.execute(*cmd, process_input=text)
        if err:
            raise RuntimeError(_('OpenSSL error: %s') % err)
        return out

    def encrypt(self, text):
        return self._run_ssl(text).strip('\n')

    def decrypt(self, text):
        return self._run_ssl(text, decrypt=True)
