import boto3
import json
import csv
import datetime
from pkg_resources import resource_filename

ReportFile = './report.csv'
CpuThreshold = 40 # If we reduce instance size, CPU utilization will be twice. e.g. 40*2=80% CPU utilization

# EC2 filter, we will skip terninated instances
custom_ec2_filter = [
    {
        'Name': 'instance-state-name',
        'Values': ['running', 'pending', 'stopping', 'stopped']
    }
]

# Search product filter. This will reduce the amount of data returned by the get_products function of the Pricing API
FLT = '[{{"Field": "tenancy", "Value": "shared", "Type": "TERM_MATCH"}},'\
      '{{"Field": "operatingSystem", "Value": "{o}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "preInstalledSw", "Value": "NA", "Type": "TERM_MATCH"}},'\
      '{{"Field": "instanceType", "Value": "{t}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "location", "Value": "{r}", "Type": "TERM_MATCH"}},'\
      '{{"Field": "capacitystatus", "Value": "Used", "Type": "TERM_MATCH"}}]'

# CSV Header
header = ['#', 'Instance', 'Region', 'OS', 'Current sizing', 'Core count', 'RAM size', 'Current pricing', 'CPU AVG 1 day', 'CPU AVG 3 days', 'CPU AVG 7 days', 'To be changed to', 'New pricing', 'Comments']

# Get list of EC2 regions
client = boto3.client('ec2')
regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
 
def main():
    with open(ReportFile, 'w', encoding='UTF8') as f:
        writer = csv.writer(f)
        writer.writerow(header)
    for region in regions:
        print("Region: " + str(region))
        # EC2 Instances
        print("Searching instances...")
        ec2 = boto3.resource('ec2', region_name = region)
        instances_list = ec2.instances.filter(Filters = custom_ec2_filter)
        InstanceNumber = 1
        for instance in instances_list:
            InstanceID = instance.id
            InstanceType = instance.instance_type
            InstanceCoreCount = instance.cpu_options['CoreCount']
            InstanceRamSize = client.describe_instance_types(InstanceTypes = [InstanceType])['InstanceTypes'][0]['MemoryInfo']['SizeInMiB']

            if InstanceRamSize >= 1024:
                InstanceRamSize = int(InstanceRamSize / 1024)
            else:
                InstanceRamSize = float(InstanceRamSize / 1024)

            InstancePlatform = instance.platform
            InstanceState = instance.state['Name']

            if InstancePlatform == 'windows':
                Platform = 'Windows'
            else:
                Platform = 'Linux'

            InstanceCost = round(float(get_price(get_region_name(region), InstanceType, Platform)) * 730, 2)

            if instance.tags != None:
                for tag in instance.tags:
                    if tag['Key'] == 'Name':
                        InstanceName = tag['Value']

            if not 'InstanceName' in locals():
                InstanceName = 'N/A'

            if InstanceState == 'stopped':
                InstanceCpuAvg1Day = None
                InstanceCpuAvg3Days = None
                InstanceCpuAvg7Days = None
                InstanceCost = 'N/A'
                InstanceNewCost = 'N/A'
                ChangedTo = None
                Comment = 'Instance is stopped'
            else:
                InstanceCpuAvg1Day = cpu_utilization(region, 86400, 1, InstanceID)
                InstanceCpuAvg3Days = cpu_utilization(region, 259200, 3, InstanceID)
                InstanceCpuAvg7Days = cpu_utilization(region, 604800, 7, InstanceID)
                if InstanceCpuAvg1Day > CpuThreshold or InstanceCpuAvg3Days > CpuThreshold or InstanceCpuAvg7Days > CpuThreshold:
                    Comment = f"Can't be reduced, as use more than {CpuThreshold}% CPU"
                    ChangedTo = None
                    InstanceNewCost = InstanceCost
                else:
                    Comment = None
                    ChangedTo = 'Possible, check RAM usage before it'
                    InstanceNewCost = None

            print(f"InstanceName: {InstanceName}")
            print(f"InstanceID: {InstanceID}")
            print(f"InstanceType: {InstanceType}")
            print(f"InstanceCoreCount: {InstanceCoreCount}")
            print(f"InstanceRamSize: {InstanceRamSize}")
            print(f"InstancePlatform: {Platform}")
            print(f"InstanceState: {InstanceState}")
            print(f"InstanceCost: {InstanceCost}")
            print("-" * 10)

            data = [
                InstanceNumber,
                InstanceName,
                region,
                Platform,
                InstanceType,
                InstanceCoreCount,
                InstanceRamSize,
                InstanceCost,
                InstanceCpuAvg1Day,
                InstanceCpuAvg3Days,
                InstanceCpuAvg7Days,
                ChangedTo,
                InstanceNewCost,
                Comment
            ]
            with open(ReportFile, 'a', encoding='UTF8') as f:
                writer = csv.writer(f)
                writer.writerow(data)
            InstanceNumber += 1
        print("-" * 50)

#
def cpu_utilization(awsRegion, period, timeRange, InstanceID):
    namespace = "AWS/EC2"
    metric = "CPUUtilization"
    statistics = "Average"
     
    client = boto3.client('cloudwatch', region_name=awsRegion)

    startTime = (datetime.datetime.now() - datetime.timedelta(days=timeRange))
    startTime = startTime.strftime("%Y-%m-%dT%H:%M:%S")
    endTime = datetime.datetime.now()
    endTime = endTime.strftime("%Y-%m-%dT%H:%M:%S")
     
    response = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        StartTime=startTime,
        EndTime=endTime,
        Period=period,
        Statistics=[
            statistics,
        ],
        Dimensions=[
            {
            'Name': 'InstanceId',
            'Value': InstanceID
            },
        ],
        Unit='Percent'
    )

    for cw_metric in response['Datapoints']:
        return round(cw_metric['Average'], 2)

# Get current AWS price for an on-demand instance
def get_price(region, instance, os):
    client = boto3.client('pricing', region_name='us-east-1')
    f = FLT.format(r=region, t=instance, o=os)
    data = client.get_products(ServiceCode='AmazonEC2', Filters=json.loads(f))
    od = json.loads(data['PriceList'][0])['terms']['OnDemand']
    id1 = list(od)[0]
    id2 = list(od[id1]['priceDimensions'])[0]
    return od[id1]['priceDimensions'][id2]['pricePerUnit']['USD']

# Translate region code to region name
def get_region_name(region_code):
    default_region = 'US East (N. Virginia)'
    endpoint_file = resource_filename('botocore', 'data/endpoints.json')
    try:
        with open(endpoint_file, 'r') as f:
            data = json.load(f)
        return data['partitions'][0]['regions'][region_code]['description'].replace('Europe', 'EU')
    except IOError:
        return default_region

if __name__ == '__main__':
    main()
