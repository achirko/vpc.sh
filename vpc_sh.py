import os
import sys
import time
import tempfile
import click
import boto.ec2
import asyncio
import asyncssh
from tabulate import tabulate
from io import StringIO
from contextlib import contextmanager
from datetime import datetime
from collections import namedtuple
from concurrent.futures import FIRST_COMPLETED

Context = namedtuple('Context', 'ec2 private_key remote_user sudo timeout')


@click.group()
@click.option('--ec2-api-url', help='EC2 api url')
@click.option('--private-key', help='Path to ssh key.', envvar='PRIVATE_KEY',
              required=True)
@click.option('--remote-user', help='Remote user name.', envvar='REMOTE_USER',
              required=True)
@click.option('--aws-region', help='AWS region.', envvar='AWS_REGION',
              default='eu-west-1')
@click.option('--aws-access-key-id', help='AWS access key id.', envvar='AWS_ACCESS_KEY_ID',
              required=True)
@click.option('--aws-secret-access-key', help='AWS secret access key.', envvar='AWS_SECRET_ACCESS_KEY',
              required=True)
@click.option(
    '--sudo', '-s', is_flag=True, help="Run command with sudo privileges.")
@click.option('--command-timeout',
              '-t',
              help='Command timeout, in seconds. 0 is no timeout.',
              default=30,
              type=int, envvar='VPCSH_TIMEOUT')
@click.pass_context
def vpc_sh(ctx, ec2_api_url, private_key, remote_user, aws_region,
           aws_access_key_id, aws_secret_access_key, sudo, command_timeout):

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

    ctx.obj = Context(conn, private_key, remote_user, sudo, command_timeout)


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

    if not sys.stdin.isatty() and cmd:
        ctx.fail("Invalid input")

    script = None
    if not sys.stdin.isatty():
        script_str = sys.stdin.read()
        script = tempfile.NamedTemporaryFile(bufsize=0)
        script.write(script_str)

    if ignore_errors:
        env.warn_only = True

    ec2_filter = {}
    for filter_str in filter:
        tag_name, tag_value = filter_str.split("=")[0], filter_str.split("=")[1]
        ec2_filter["tag:{}".format(tag_name)] = tag_value

    instances = [
        instance
        for instance in ctx.obj.ec2.get_only_instances(filters=ec2_filter)
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

    ioloop = asyncio.get_event_loop()
    ioloop.run_until_complete(run_command_async(ctx, instances, ioloop, cmd))
    ioloop.close()


async def run_command_async(ctx, instances, ioloop, cmd):
    async def run_client(host, user, private_key, command, sudo=False):
        cmd = "bash -c '{} 2>&1'".format(command)
        if sudo:
            cmd = 'sudo ' + cmd
        async with asyncssh.connect(
                host, username=user,
                client_keys=[private_key],
                known_hosts=None
        ) as conn:
            return await conn.run(command)

    commands = []
    Command = namedtuple('Command', 'instance task')
    for instance in instances:
        commands.append(Command(
            instance,
            ioloop.create_task(
                run_client(instance.private_ip_address, ctx.obj.remote_user,
                           ctx.obj.private_key, cmd, ctx.obj.sudo)
            )
        ))
    pending = [c.task for c in commands]
    while True:
        done, pending = await asyncio.wait(
            pending, timeout=ctx.obj.timeout, return_when=FIRST_COMPLETED)

        if done:
            for c in commands:
                if c.task.done():
                    commands.remove(c)
                    table = tabulate(
                        [[c.instance.tags.get('Name', ''),
                          c.instance.id, c.instance.private_ip_address]],
                        tablefmt='simple'
                    )
                    click.secho(table, fg='green', bold=True)
                    click.secho(c.task.result().stdout, fg='green')
                    click.echo()
        else:
            for c in commands:
                c.task.cancel()
                table = tabulate(
                    [[c.instance.tags.get('Name', ''),
                      c.instance.id, c.instance.private_ip_address]],
                    tablefmt='simple'
                )
                click.secho(table, fg='red', bold=True)
                click.secho('Timeout', fg='red')
                click.echo()
            return


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
                    put(script.name, remote_folder, mode='0755')
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
