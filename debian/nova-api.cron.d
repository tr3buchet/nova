#
# Cron script to generate usage notifications for instances neither 
# created nor destroyed in a given time period.
#
# installed with the nova-api package
#
#

MAILTO=root

0 */6 * * *     /usr/bin/instance-usage-audit > /dev/null