# CLI tool to run commands on ec2 instances.

## Supported cloud platforms

- [x] AWS EC2
- [ ] Openstack via ec2 API endpoint

## Features

- [x] Multiple remote users (if can't authenticate - will try next username)
- [x] Filter by tags
- [x] Parallel execution
- [ ] Profiles (group settings and filters in named profile, and specify profile on `vpc.py run`)
- [ ] Filter by launch date (`launched-before` and `launched-after` )
- [ ] Filter by keypair
- [ ] --dry-run flag

## Installation

Install the latest stable version:

```
pip install vpc.py
```

## Usage

Get help:

```
vpc.py --help
vpc.py run --help
vpc.py run-one --help
```

Create settings file:

```
mkdir ~/.vpc.py
cat >>~/.vpc.py/settings<<-EOF
[default]
remote_user = ubuntu,centos,root
private_key = /home/ubuntu/.ssh/ec2.pem
aws_access_key_id = access-key
aws_secret_access_key = secret-access-key
aws_region = eu-west-1
EOF
```

Filter by tag 'owner=automation' and run command on resulted instances:

```
vpc.py run -f owner=automation 'df -h'
```

Run script:

```
vpc.py run -f owner=automation<<-EOF
echo hello
uname -a
EOF
```

Run any script or binary (assuming it's compatible with target hosts):

```
vpc.py run -f owner=automation < some_python_script.py
vpc.py run -f owner=automation < /usr/local/bin/weather
```
