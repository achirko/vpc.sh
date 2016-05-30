CLI tool to run shell commands on ec2 instances. Built with glorious click, boto and fabric.

## Supported cloud platforms

- [x] AWS EC2
- [ ] Openstack via ec2 API endpoint

## Features

- [x] Multiple remote users (if can't authenticate - try next username)
- [x] Filter by tags
- [x] Parallel execution
- [ ] Filter by launched date (`launched-before` and `launched-after` )
- [ ] Filter by keypair
- [ ] Profiles (group settings and filters in named profile, and specify profile on vpc.sh run)
- [ ] `vpc.sh list` command - similar to `vpc.sh run`, but only prints info about instances
- [ ] heredocs-style multiple commands
- [ ] PyPI

## Usage
