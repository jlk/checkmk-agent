"""Command-line interface for Checkmk LLM Agent."""

import sys
import click
import logging
from typing import Optional

from .config import load_config
from .api_client import CheckmkClient
from .llm_client import create_llm_client, LLMProvider
from .host_operations import HostOperationsManager
from .utils import setup_logging


@click.group()
@click.option('--log-level', default=None, help='Logging level (DEBUG, INFO, WARNING, ERROR)')
@click.option('--config', '--config-file', help='Path to configuration file (YAML, TOML, or JSON)')
@click.pass_context
def cli(ctx, log_level: str, config: Optional[str]):
    """Checkmk LLM Agent - Natural language interface for Checkmk."""
    ctx.ensure_object(dict)

    # Load configuration first (to get config log_level if CLI flag not set)
    from .config import load_config
    app_config = load_config(config_file=config)
    ctx.obj['config'] = app_config

    # Determine log level: CLI flag overrides config
    effective_log_level = log_level or app_config.log_level

    # Setup logging
    from .logging_utils import setup_logging
    setup_logging(effective_log_level)
    logger = logging.getLogger(__name__)

    try:
        # Initialize clients
        from .api_client import CheckmkClient
        checkmk_client = CheckmkClient(app_config.checkmk)
        ctx.obj['checkmk_client'] = checkmk_client

        # Try to initialize LLM client
        try:
            from .llm_client import create_llm_client
            llm_client = create_llm_client(app_config.llm)
            ctx.obj['llm_client'] = llm_client

            # Initialize host operations manager
            from .host_operations import HostOperationsManager
            host_manager = HostOperationsManager(checkmk_client, llm_client, app_config)
            ctx.obj['host_manager'] = host_manager

        except Exception as e:
            logger.warning(f"LLM client initialization failed: {e}")
            ctx.obj['llm_client'] = None
            ctx.obj['host_manager'] = None

    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        import click
        click.echo(f"❌ Error: {e}", err=True)
        import sys
        sys.exit(1)


@cli.command()
@click.pass_context
def test(ctx):
    """Test connection to Checkmk API."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        if checkmk_client.test_connection():
            click.echo("✅ Successfully connected to Checkmk API")
        else:
            click.echo("❌ Failed to connect to Checkmk API")
            sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Connection test failed: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def interactive(ctx):
    """Start interactive mode for natural language commands."""
    host_manager = ctx.obj.get('host_manager')
    
    if not host_manager:
        click.echo("❌ LLM client not available. Check your API keys in .env file.", err=True)
        sys.exit(1)
    
    click.echo("🤖 Checkmk LLM Agent - Interactive Mode")
    click.echo("=" * 40)
    click.echo("You can ask me to:")
    click.echo("- List hosts: 'show all hosts', 'list hosts in folder /web'")
    click.echo("- Create hosts: 'create host server01 in folder /'")
    click.echo("- Delete hosts: 'delete host server01'")
    click.echo("- Get host details: 'show details for server01'")
    click.echo("\nType 'exit' or 'quit' to exit, 'help' for more commands.\n")
    
    while True:
        try:
            user_input = input("🔧 checkmk> ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ['exit', 'quit', 'q']:
                click.echo("👋 Goodbye!")
                break
            
            if user_input.lower() in ['help', 'h']:
                show_help()
                continue
            
            if user_input.lower() == 'stats':
                result = host_manager.get_host_statistics()
                click.echo(result)
                continue
            
            if user_input.lower() == 'test':
                result = host_manager.test_connection()
                click.echo(result)
                continue
            
            # Process the command
            result = host_manager.process_command(user_input)
            click.echo(result)
            
        except KeyboardInterrupt:
            click.echo("\n👋 Goodbye!")
            break
        except EOFError:
            click.echo("\n👋 Goodbye!")
            break
        except Exception as e:
            click.echo(f"❌ Error: {e}", err=True)


@cli.group()
def hosts():
    """Host management commands."""
    pass


@hosts.command('list')
@click.option('--folder', help='Filter by folder')
@click.option('--search', help='Search term to filter hosts')
@click.option('--effective-attributes', is_flag=True, help='Show effective attributes')
@click.pass_context
def list_hosts(ctx, folder: Optional[str], search: Optional[str], effective_attributes: bool):
    """List all hosts or filter by criteria."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        hosts = checkmk_client.list_hosts(effective_attributes=effective_attributes)
        
        # Apply filters
        if folder or search:
            filtered_hosts = []
            for host in hosts:
                host_id = host.get("id", "")
                extensions = host.get("extensions", {})
                host_folder = extensions.get("folder", "")
                attributes = extensions.get("attributes", {})
                alias = attributes.get("alias", "")
                
                # Filter by folder
                if folder and folder not in host_folder:
                    continue
                
                # Filter by search term
                if search:
                    search_lower = search.lower()
                    if not any(search_lower in field.lower() for field in [host_id, host_folder, alias]):
                        continue
                
                filtered_hosts.append(host)
            
            hosts = filtered_hosts
        
        if not hosts:
            click.echo("No hosts found.")
            return
        
        # Display hosts
        click.echo(f"Found {len(hosts)} hosts:")
        for host in hosts:
            host_id = host.get("id", "Unknown")
            extensions = host.get("extensions", {})
            host_folder = extensions.get("folder", "Unknown")
            attributes = extensions.get("attributes", {})
            ip_address = attributes.get("ipaddress", "Not set")
            
            click.echo(f"  📦 {host_id}")
            click.echo(f"     Folder: {host_folder}")
            click.echo(f"     IP: {ip_address}")
            if extensions.get("is_cluster"):
                click.echo(f"     Type: Cluster")
            if extensions.get("is_offline"):
                click.echo(f"     Status: Offline")
            click.echo()
            
    except Exception as e:
        click.echo(f"❌ Error listing hosts: {e}", err=True)
        sys.exit(1)


@hosts.command('create')
@click.argument('host_name')
@click.option('--folder', default='/', help='Folder path (default: /)')
@click.option('--ip', help='IP address')
@click.option('--alias', help='Host alias/description')
@click.option('--bake-agent', is_flag=True, help='Automatically bake agent')
@click.pass_context
def create_host(ctx, host_name: str, folder: str, ip: Optional[str], 
                alias: Optional[str], bake_agent: bool):
    """Create a new host."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        attributes = {}
        if ip:
            attributes['ipaddress'] = ip
        if alias:
            attributes['alias'] = alias
        
        result = checkmk_client.create_host(
            folder=folder,
            host_name=host_name,
            attributes=attributes,
            bake_agent=bake_agent
        )
        
        click.echo(f"✅ Successfully created host: {host_name}")
        click.echo(f"   Folder: {folder}")
        if attributes:
            click.echo(f"   Attributes: {attributes}")
            
    except Exception as e:
        click.echo(f"❌ Error creating host: {e}", err=True)
        sys.exit(1)


@hosts.command('delete')
@click.argument('host_name')
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
@click.pass_context
def delete_host(ctx, host_name: str, force: bool):
    """Delete a host."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        # Check if host exists
        try:
            host = checkmk_client.get_host(host_name)
            click.echo(f"Host found: {host_name}")
            extensions = host.get("extensions", {})
            folder = extensions.get("folder", "Unknown")
            click.echo(f"Folder: {folder}")
        except Exception as e:
            click.echo(f"❌ Host '{host_name}' not found: {e}", err=True)
            sys.exit(1)
        
        # Confirmation
        if not force:
            if not click.confirm(f"Are you sure you want to delete host '{host_name}'?"):
                click.echo("❌ Deletion cancelled.")
                return
        
        checkmk_client.delete_host(host_name)
        click.echo(f"✅ Successfully deleted host: {host_name}")
        
    except Exception as e:
        click.echo(f"❌ Error deleting host: {e}", err=True)
        sys.exit(1)


@hosts.command('get')
@click.argument('host_name')
@click.option('--effective-attributes', is_flag=True, help='Show effective attributes')
@click.pass_context
def get_host(ctx, host_name: str, effective_attributes: bool):
    """Get detailed information about a host."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        host = checkmk_client.get_host(host_name, effective_attributes=effective_attributes)
        
        host_id = host.get("id", "Unknown")
        extensions = host.get("extensions", {})
        folder = extensions.get("folder", "Unknown")
        attributes = extensions.get("attributes", {})
        
        click.echo(f"📦 Host Details: {host_id}")
        click.echo(f"   Folder: {folder}")
        click.echo(f"   Cluster: {'Yes' if extensions.get('is_cluster') else 'No'}")
        click.echo(f"   Offline: {'Yes' if extensions.get('is_offline') else 'No'}")
        
        if attributes:
            click.echo("   Attributes:")
            for key, value in attributes.items():
                click.echo(f"     {key}: {value}")
        
        if effective_attributes and extensions.get("effective_attributes"):
            click.echo("   Effective Attributes:")
            for key, value in extensions["effective_attributes"].items():
                click.echo(f"     {key}: {value}")
        
    except Exception as e:
        click.echo(f"❌ Error getting host: {e}", err=True)
        sys.exit(1)


@hosts.command('interactive-create')
@click.pass_context
def interactive_create(ctx):
    """Create a host with interactive prompts."""
    host_manager = ctx.obj.get('host_manager')
    
    if not host_manager:
        click.echo("❌ Host manager not available.", err=True)
        sys.exit(1)
    
    result = host_manager.interactive_create_host()
    click.echo(result)


@cli.group()
def rules():
    """Rule management commands."""
    pass


@rules.command('list')
@click.argument('ruleset_name')
@click.pass_context
def list_rules(ctx, ruleset_name: str):
    """List all rules in a specific ruleset."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        rules = checkmk_client.list_rules(ruleset_name)
        
        if not rules:
            click.echo(f"No rules found in ruleset: {ruleset_name}")
            return
        
        # Display rules
        click.echo(f"Found {len(rules)} rules in ruleset '{ruleset_name}':")
        for rule in rules:
            rule_id = rule.get("id", "Unknown")
            extensions = rule.get("extensions", {})
            folder = extensions.get("folder", "Unknown")
            properties = extensions.get("properties", {})
            disabled = properties.get("disabled", False)
            description = properties.get("description", "")
            
            click.echo(f"  📋 {rule_id}")
            click.echo(f"     Folder: {folder}")
            click.echo(f"     Status: {'Disabled' if disabled else 'Enabled'}")
            if description:
                click.echo(f"     Description: {description}")
            click.echo()
            
    except Exception as e:
        click.echo(f"❌ Error listing rules: {e}", err=True)
        sys.exit(1)


@rules.command('create')
@click.argument('ruleset_name')
@click.option('--folder', default='/', help='Folder path (default: /)')
@click.option('--value', help='Rule value as JSON string')
@click.option('--description', help='Rule description')
@click.option('--disabled', is_flag=True, help='Create rule as disabled')
@click.pass_context
def create_rule(ctx, ruleset_name: str, folder: str, value: Optional[str], 
                description: Optional[str], disabled: bool):
    """Create a new rule in a ruleset."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        # If value not provided, prompt for it
        if not value:
            value = click.prompt("Enter rule value as JSON string")
        
        # Build properties
        properties = {}
        if description:
            properties['description'] = description
        if disabled:
            properties['disabled'] = True
        
        result = checkmk_client.create_rule(
            ruleset=ruleset_name,
            folder=folder,
            value_raw=value,
            properties=properties
        )
        
        rule_id = result.get("id", "Unknown")
        click.echo(f"✅ Successfully created rule: {rule_id}")
        click.echo(f"   Ruleset: {ruleset_name}")
        click.echo(f"   Folder: {folder}")
        if properties:
            click.echo(f"   Properties: {properties}")
            
    except Exception as e:
        click.echo(f"❌ Error creating rule: {e}", err=True)
        sys.exit(1)


@rules.command('delete')
@click.argument('rule_id')
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
@click.pass_context
def delete_rule(ctx, rule_id: str, force: bool):
    """Delete a rule."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        # Check if rule exists
        try:
            rule = checkmk_client.get_rule(rule_id)
            extensions = rule.get("extensions", {})
            ruleset = extensions.get("ruleset", "Unknown")
            folder = extensions.get("folder", "Unknown")
            click.echo(f"Rule found: {rule_id}")
            click.echo(f"Ruleset: {ruleset}")
            click.echo(f"Folder: {folder}")
        except Exception as e:
            click.echo(f"❌ Rule '{rule_id}' not found: {e}", err=True)
            sys.exit(1)
        
        # Confirmation
        if not force:
            if not click.confirm(f"Are you sure you want to delete rule '{rule_id}'?"):
                click.echo("❌ Deletion cancelled.")
                return
        
        checkmk_client.delete_rule(rule_id)
        click.echo(f"✅ Successfully deleted rule: {rule_id}")
        
    except Exception as e:
        click.echo(f"❌ Error deleting rule: {e}", err=True)
        sys.exit(1)


@rules.command('get')
@click.argument('rule_id')
@click.pass_context
def get_rule(ctx, rule_id: str):
    """Get detailed information about a rule."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        rule = checkmk_client.get_rule(rule_id)
        
        rule_id = rule.get("id", "Unknown")
        extensions = rule.get("extensions", {})
        ruleset = extensions.get("ruleset", "Unknown")
        folder = extensions.get("folder", "Unknown")
        properties = extensions.get("properties", {})
        value_raw = extensions.get("value_raw", "")
        
        click.echo(f"📋 Rule Details: {rule_id}")
        click.echo(f"   Ruleset: {ruleset}")
        click.echo(f"   Folder: {folder}")
        click.echo(f"   Status: {'Disabled' if properties.get('disabled') else 'Enabled'}")
        
        if properties.get("description"):
            click.echo(f"   Description: {properties['description']}")
        
        if value_raw:
            click.echo(f"   Value: {value_raw}")
        
        if extensions.get("conditions"):
            click.echo(f"   Conditions: {extensions['conditions']}")
        
    except Exception as e:
        click.echo(f"❌ Error getting rule: {e}", err=True)
        sys.exit(1)


@rules.command('move')
@click.argument('rule_id')
@click.argument('position', type=click.Choice(['top_of_folder', 'bottom_of_folder', 'before', 'after']))
@click.option('--folder', help='Target folder for the rule')
@click.option('--target-rule', help='Target rule ID for before/after positioning')
@click.pass_context
def move_rule(ctx, rule_id: str, position: str, folder: Optional[str], target_rule: Optional[str]):
    """Move a rule to a new position."""
    checkmk_client = ctx.obj['checkmk_client']
    
    try:
        if position in ['before', 'after'] and not target_rule:
            raise ValueError(f"--target-rule is required when position is '{position}'")
        
        result = checkmk_client.move_rule(
            rule_id=rule_id,
            position=position,
            folder=folder,
            target_rule_id=target_rule
        )
        
        click.echo(f"✅ Successfully moved rule: {rule_id}")
        click.echo(f"   Position: {position}")
        if folder:
            click.echo(f"   Target folder: {folder}")
        if target_rule:
            click.echo(f"   Target rule: {target_rule}")
        
    except Exception as e:
        click.echo(f"❌ Error moving rule: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def stats(ctx):
    """Show host statistics."""
    host_manager = ctx.obj.get('host_manager')
    
    if not host_manager:
        # Fallback to basic stats without LLM
        checkmk_client = ctx.obj['checkmk_client']
        try:
            hosts = checkmk_client.list_hosts()
            click.echo(f"📊 Total hosts: {len(hosts)}")
        except Exception as e:
            click.echo(f"❌ Error getting statistics: {e}", err=True)
        return
    
    result = host_manager.get_host_statistics()
    click.echo(result)


def show_help():
    """Show detailed help information."""
    click.echo("""
🔧 Available Commands:

Natural Language Commands:
  - "list all hosts" / "show hosts"
  - "create host server01 in folder /web"
  - "delete host server01"
  - "show details for server01"

Special Commands:
  - help/h        Show this help
  - stats         Show host statistics
  - test          Test API connection
  - exit/quit/q   Exit interactive mode

Examples:
  🔧 checkmk> list all hosts
  🔧 checkmk> create host web01 with ip 192.168.1.10
  🔧 checkmk> delete server01
  🔧 checkmk> show me details for web01
""")


if __name__ == '__main__':
    cli()