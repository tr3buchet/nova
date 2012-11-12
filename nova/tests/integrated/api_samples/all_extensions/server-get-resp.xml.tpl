<?xml version='1.0' encoding='UTF-8'?>
<server xmlns:OS-DCF="http://docs.openstack.org/compute/ext/disk_config/api/v1.1" xmlns:OS-EXT-SRV-ATTR="http://docs.openstack.org/compute/ext/extended_status/api/v1.1" xmlns:rax-bandwidth="http://docs.rackspace.com/servers/api/ext/server_bandwidth/" xmlns:OS-EXT-STS="http://docs.openstack.org/compute/ext/extended_status/api/v1.1" xmlns:atom="http://www.w3.org/2005/Atom" xmlns="http://docs.openstack.org/compute/api/v1.1" status="ACTIVE" updated="%(timestamp)s" hostId="%(hostid)s" name="new-server-test" created="%(timestamp)s" userId="fake" tenantId="openstack" accessIPv4="" accessIPv6="" progress="0" id="%(id)s" key_name="None" config_drive="" OS-EXT-SRV-ATTR:vm_state="active" OS-EXT-SRV-ATTR:task_state="None" OS-EXT-SRV-ATTR:power_state="1" OS-EXT-SRV-ATTR:instance_name="instance-00000001" OS-EXT-SRV-ATTR:host="%(compute_host)s" OS-EXT-SRV-ATTR:hypervisor_hostname="%(hypervisor_hostname)s" OS-DCF:diskConfig="AUTO">
  <image id="%(uuid)s">
    <atom:link href="%(host)s/openstack/images/%(uuid)s" rel="bookmark"/>
  </image>
  <flavor id="1">
    <atom:link href="%(host)s/openstack/flavors/1" rel="bookmark"/>
  </flavor>
  <metadata>
    <meta key="My Server Name">Apache1</meta>
  </metadata>
  <addresses>
    <network id="private">
      <ip version="4" addr="%(ip)s"/>
    </network>
  </addresses>
  <atom:link href="%(host)s/v2/openstack/servers/%(id)s" rel="self"/>
  <atom:link href="%(host)s/openstack/servers/%(id)s" rel="bookmark"/>
  <security_groups>
    <security_group name="default"/>
  </security_groups>
  <rax-bandwidth:bandwidth/>
</server>
