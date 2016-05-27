import os
import sys
from atexit import register as run_on_exit
import ConfigParser
import click
import boto.ec2
from fabric.api import env, run, settings
from fabric.api import sudo as run_sudo
from fabric import exceptions as fabric_exc
from tabulate import tabulate
from cStringIO import StringIO
from contextlib import contextmanager
import multiprocessing

SETTINGS_FILE = "~/.vpc.sh/settings"


class PromptException(Exception):
    pass


@click.group()
@click.option('--private-key', help='Path to ssh key.')
@click.option('--remote-user', help='Remote user name')
@click.option('--aws-region', help='AWS region.')
@click.option('--aws-access-key-id', help='AWS access key id.')
@click.option('--aws-secret-access-key', help='AWS secret access key.')
@click.option('--sudo', is_flag=True, help="Run command with sudo privileges")
@click.option('--command-timeout', help='Command timeout, in seconds', type=int)
@click.pass_context
def vpc_sh(ctx, private_key, remote_user, aws_region, aws_access_key_id,
           aws_secret_access_key, sudo, command_timeout):
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
    env.key_filename = private_key
    env.skip_bad_hosts = True
    env.abort_on_prompts = True
    env.abort_exception = PromptException
    env.disable_known_hosts = True
    env.colorize_errors = True
    if command_timeout:
        env.command_timeout = command_timeout

    conn = boto.ec2.connect_to_region(
        aws_region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key)
    run_on_exit(conn.close)

    ctx.obj = dict(aws_conn=conn)
    ctx.obj['private_key'] = private_key
    ctx.obj['remote_user'] = remote_user
    ctx.obj['sudo'] = sudo


@vpc_sh.command("run-all")
@click.option('-f', '--filter', multiple=True,
              help='Filter instances by tags, in form of "tag-name=tag-value"')
@click.option('--skip', multiple=True, help="Instance id to skip.")
@click.option('--only', multiple=True, help="Instance id to run command on.")
@click.option('--ignore-errors', is_flag=True,
              help="Don't exit when one of the hosts failed to successfully "
                   "execute the command.")
@click.argument("cmd")
@click.pass_context
def run_all(ctx, filter, cmd, skip, only, ignore_errors):
    if ignore_errors:
        env.warn_only = True

    ec2_filter = {}
    for filter_str in filter:
        tag_name, tag_value = filter_str.split("=")[0], filter_str.split("=")[1]
        ec2_filter["tag:{}".format(tag_name)] = tag_value

    instances = [
        instance
        for instance in ctx.obj['aws_conn'].get_only_instances(filters=ec2_filter)
        if instance.state == "running"
    ]

    pool_inputs = []
    for instance in instances:
        if instance.id in skip or (only and instance.id not in only):
            continue
        pool_inputs.append(
            (ctx.obj['remote_user'],
             ctx.obj['sudo'],
             cmd,
             instance.tags.get('Name', ''),
             instance.id,
             instance.private_ip_address,)
        )

    pool_size = multiprocessing.cpu_count() * 2
    lock = multiprocessing.Lock()
    pool = multiprocessing.Pool(processes=pool_size,
                                initializer=mp_init_lock,
                                initargs=(lock,))
    pool.map(mp_run_command_wrapper, pool_inputs)
    pool.close()
    pool.join()

    # for instance in instances:
    #     if instance.id in skip or (only and instance.id not in only):
    #         continue
    #     run_command(ctx, instance, cmd)


@vpc_sh.command("run-one")
@click.argument("instance-id")
@click.argument("cmd")
@click.pass_context
def run_one(ctx, instance_id, cmd):
    instance = ctx.obj['aws_conn'].get_only_instances(instance_ids=[instance_id])[0]
    run_command(ctx, instance, cmd)


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


def run_command(remote_user, sudo, cmd, instance_name, instance_id, instance_ip):
    table = tabulate([[instance_name, instance_id, instance_ip]],
                     tablefmt='simple')
    click.echo()
    click.secho(table, fg='green', bold=True)
    for user in remote_user:
        host_string = "{}@{}".format(user, instance_ip)
        click.secho("try {}".format(host_string), fg='green')
        with settings(host_string=host_string):
            try:
                if sudo:
                    run_sudo(cmd)
                else:
                    run(cmd)
            # TODO fabric.exceptions.NetworkError
            except fabric_exc.CommandTimeout as e:
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
