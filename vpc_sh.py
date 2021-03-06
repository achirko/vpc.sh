import os
import sys
import time
import tempfile
from atexit import register as run_on_exit
import ConfigParser
import click
import boto.ec2
from fabric.api import env, run, settings, put
from fabric.api import sudo as run_sudo
from fabric import exceptions as fabric_exc
from tabulate import tabulate
from cStringIO import StringIO
from contextlib import contextmanager
import multiprocessing
from datetime import datetime
from collections import namedtuple

SETTINGS_FILE = "~/.vpc.sh/settings"


class PromptException(Exception):
    pass


@click.group()
@click.option('--ec2-api-url', help='EC2 api url')
@click.option('--private-key', help='Path to ssh key.')
@click.option('--remote-user', help='Remote user name.')
@click.option('--aws-region', help='AWS region.')
@click.option('--aws-access-key-id', help='AWS access key id.')
@click.option('--aws-secret-access-key', help='AWS secret access key.')
@click.option(
    '--sudo', '-s', is_flag=True, help="Run command with sudo privileges.")
@click.option(
    '--parallel', '-p', is_flag=True, help="Run all commands in parallel.")
@click.option('--command-timeout',
              '-t',
              help='Command timeout, in seconds. 0 is no timeout.',
              default=60,
              type=int)
@click.pass_context
def vpc_sh(ctx, ec2_api_url, private_key, remote_user, aws_region,
           aws_access_key_id, aws_secret_access_key, sudo, parallel,
           command_timeout):
    cfg = ConfigParser.RawConfigParser()
    cfg.read(os.path.expanduser(SETTINGS_FILE))

    private_key = private_key or cfg.get('default', 'private_key')
    if not private_key:
        ctx.fail("Please specify path to private ssh key.")

    remote_user = remote_user or cfg.get('default', 'remote_user')
    if not remote_user:
        ctx.fail("Please specify remote user name.")
    remote_user = remote_user.split(',')

    aws_region = aws_region or cfg.get('default', 'aws_region')
    aws_access_key_id = \
        aws_access_key_id or cfg.get('default', 'aws_access_key_id')
    aws_secret_access_key = \
        aws_secret_access_key or cfg.get('default', 'aws_secret_access_key')
    ec2_api_url = (
        ec2_api_url or
        cfg.get('default', 'ec2_api_url')
        if cfg.has_option('default', 'ec2_api_url')
        else None
    )

    env.key_filename = private_key
    env.skip_bad_hosts = True
    env.abort_on_prompts = True
    env.abort_exception = PromptException
    env.disable_known_hosts = True
    env.colorize_errors = True
    if command_timeout:
        env.command_timeout = command_timeout

    if ec2_api_url:
        conn = boto.connect_ec2_endpoint(
            url=ec2_api_url,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key)
    else:
        conn = boto.ec2.connect_to_region(
            aws_region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key)
    run_on_exit(conn.close)

    ctx.obj = dict(aws_conn=conn)
    ctx.obj['private_key'] = private_key
    ctx.obj['remote_user'] = remote_user
    ctx.obj['sudo'] = sudo
    ctx.obj['parallel'] = parallel


@vpc_sh.command("run")
@click.option('-f', '--filter', multiple=True,
              help='Filter instances by tags, in form of "tag-name=tag-value"')
@click.option('--skip', multiple=True, help="Instance id to skip.")
@click.option('--ignore-errors', is_flag=True,
              help="Don't exit when one of the hosts failed to successfully "
                   "execute the command.")
@click.option('--launched-before',
              help='Choose instances launched before some date. Date format: year-month-day(%Y-%m-%d)')
@click.option('--launched-after',
              help='Choose instances launched after some date. Date format: year-month-day(%Y-%m-%d)')
@click.argument("cmd", required=False)
@click.pass_context
def run_all(ctx, filter, cmd, skip, ignore_errors, launched_before, launched_after):
    Instance = namedtuple('Instance', 'name id hostname')
    script = None
    instances = []
    if not sys.stdin.isatty() and cmd:
        click.echo('Read inventiry from stdin')
        instances = [
            Instance(item, item, item) for item in sys.stdin.read().split()
        ]
    elif not sys.stdin.isatty():
        click.echo('Read command from stdin')
        script_str = sys.stdin.read()
        script = tempfile.NamedTemporaryFile(bufsize=0)
        script.write(script_str)

    if ignore_errors:
        env.warn_only = True

    ec2_filter = {}
    for filter_str in filter:
        tag_name, tag_value = filter_str.split("=")[0], filter_str.split("=")[1]
        ec2_filter["tag:{}".format(tag_name)] = tag_value

    if not instances:
        instances = [
            instance
            for instance in ctx.obj['aws_conn'].get_only_instances(filters=ec2_filter)
            if instance.state == "running" and instance.id not in skip
        ]

        if launched_after or launched_before:
            instances_by_date = []
            if not launched_before:
                launched_before = '9999-1-1'
            if not launched_after:
                launched_after = '1970-1-1'
            for i in instances:
                if datetime.strptime(launched_before, '%Y-%m-%d').date() > \
                        datetime.strptime(i.launch_time, '%Y-%m-%dT%H:%M:%S.%fZ').date() >= \
                        datetime.strptime(launched_after, '%Y-%m-%d').date():
                    instances_by_date.append(i)
            instances = instances_by_date

        if len(instances) == 0:
            click.secho("No instances to satisfy provided filters.", fg='blue')
            ctx.exit()
        instances = map(
            lambda i: Instance(i.tags.get('Name'), i.id, i.private_ip_address),
            instances
        )

    if ctx.obj['parallel']:
        pool_inputs = []
        for instance in instances:
            pool_inputs.append(
                (ctx.obj['remote_user'],
                 ctx.obj['sudo'],
                 cmd,
                 instance.name,
                 instance.id,
                 instance.hostname,
                 script,)
            )

        pool_size = len(pool_inputs)
        lock = multiprocessing.Lock()
        pool = multiprocessing.Pool(processes=pool_size,
                                    initializer=mp_init_lock,
                                    initargs=(lock,))
        pool.map(mp_run_command_wrapper, pool_inputs)
        pool.close()
        pool.join()
    else:
        for instance in instances:
            run_command(
                ctx.obj['remote_user'],
                ctx.obj['sudo'],
                cmd,
                instance.name,
                instance.id,
                instance.hostname,
                script
            )


@vpc_sh.command("run-one")
@click.argument("instance-id")
@click.argument("cmd", required=False)
@click.pass_context
def run_one(ctx, instance_id, cmd):
    if not sys.stdin.isatty() and cmd:
        ctx.fail("Invalid input")

    script = None
    if not sys.stdin.isatty():
        script_str = sys.stdin.read()
        script = tempfile.NamedTemporaryFile(bufsize=0)
        os.chmod(script.name, 0666)
        script.write(script_str)

    instance = ctx.obj['aws_conn'].get_only_instances(instance_ids=[instance_id])[0]
    run_command(
        ctx.obj['remote_user'], ctx.obj['sudo'], cmd,
        instance.tags.get('Name', ''), instance.id,
        instance.private_ip_address, script
    )


def mp_init_lock(l):
    global lock
    lock = l


def mp_run_command_wrapper(args):
    @contextmanager
    def synchronize_stdout():
        stringio = StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stringio, stringio
        try:
            yield
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
            lock.acquire()
            # TODO preserve output colors
            sys.stdout.write(stringio.getvalue())
            sys.stdout.flush()
            lock.release()

    with synchronize_stdout():
        return run_command(*args)


def run_command(remote_user, sudo, cmd, instance_name, instance_id,
                instance_ip, script=None):
    table = tabulate([[instance_name, instance_id, instance_ip]],
                     tablefmt='simple')
    click.echo()
    click.secho(table, fg='green', bold=True)
    for user in remote_user:
        host_string = "{}@{}".format(user, instance_ip)
        click.secho("try {}".format(host_string), fg='green')
        with settings(host_string=host_string):
            try:
                if script:
                    remote_folder = "/tmp/vpc.sh/{}".format(time.time())
                    remote_script = os.path.join(
                        remote_folder, os.path.basename(script.name)
                    )
                    run("mkdir -p {}".format(remote_folder))
                    put(script.name, remote_folder, mode=0755)
                    cmd = remote_script
                if sudo:
                    run_sudo(cmd)
                else:
                    run(cmd)
            except (fabric_exc.CommandTimeout, fabric_exc.NetworkError) as e:
                click.secho(str(e), fg='red')
                break
            except PromptException:
                # try next user
                continue
            else:
                # success
                # go to the next instance
                break

if __name__ == "__main__":
    vpc_sh()
