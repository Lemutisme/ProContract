use std::io::Read;
use std::path::PathBuf;

use clap::Parser;
use clap::Subcommand;
use codex_pro_contract_store::cli;

#[derive(Parser)]
#[command(about = "Operator access to a ProContract store")]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Capture a workspace and print its subject hash, manifest and capture policy as JSON.
    Capture {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        root: PathBuf,
        #[arg(long = "exclude")]
        exclude: Vec<String>,
    },
    /// Write a captured subject into a directory.
    Materialize {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        subject: String,
        #[arg(long)]
        dest: PathBuf,
    },
    /// Append an experiment event read as JSON from stdin.
    Append {
        #[arg(long)]
        store: PathBuf,
    },
    /// Print all experiment events as a JSON array.
    Events {
        #[arg(long)]
        store: PathBuf,
    },
    /// Exit zero when the experiment chain verifies.
    Verify {
        #[arg(long)]
        store: PathBuf,
    },
    /// Apply an authenticated kernel command read as JSON from stdin; print the new contract state.
    Apply {
        #[arg(long)]
        store: PathBuf,
        /// Idempotency key: replaying the same command under the same key is a no-op.
        #[arg(long)]
        key: String,
    },
    /// Atomically discharge and select an incumbent using a fenced adoption request from stdin.
    Adopt {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        key: String,
    },
    /// Record a new incumbent selection of a live acceptance from a fenced request on stdin; print
    /// its sequence number.
    Select {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        key: String,
    },
    /// Register adoptions made before acceptances were recorded, from a request on stdin that the
    /// store verifies against its own records; print what was registered.
    Migrate {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        key: String,
    },
    /// Print every acceptance the store recorded, with its liveness, as a JSON array.
    Acceptances {
        #[arg(long)]
        store: PathBuf,
    },
    /// Print the current selection of an incumbent and its acceptance (`null` before any).
    Incumbent {
        #[arg(long)]
        store: PathBuf,
    },
    /// Print whether the acceptance recorded by an adopting selection is live.
    Live {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        selection: i64,
    },
    /// Print a contract's current state as JSON (`null` when it does not exist).
    Contract {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        id: String,
    },
    /// Print the domain-separated digest of a JSON value read from stdin.
    Digest {
        #[arg(long)]
        domain: String,
    },
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> cli::CliResult<()> {
    match Args::parse().command {
        Command::Capture {
            store,
            root,
            exclude,
        } => {
            let captured = cli::capture_command(&store, &root, &exclude).await?;
            println!("{}", serde_json::to_string(&captured)?);
        }
        Command::Materialize {
            store,
            subject,
            dest,
        } => {
            cli::materialize_command(&store, &subject, &dest).await?;
        }
        Command::Append { store } => {
            let mut event = String::new();
            std::io::stdin().read_to_string(&mut event)?;
            let appended = cli::append_command(&store, &event).await?;
            println!("{}", serde_json::to_string(&appended)?);
        }
        Command::Events { store } => {
            let events = cli::events_command(&store).await?;
            println!("{}", serde_json::to_string(&events)?);
        }
        Command::Verify { store } => cli::verify_command(&store).await?,
        Command::Apply { store, key } => {
            let mut command = String::new();
            std::io::stdin().read_to_string(&mut command)?;
            let contract = cli::apply_command(&store, &key, &command).await?;
            println!("{}", serde_json::to_string(&contract)?);
        }
        Command::Adopt { store, key } => {
            let mut request = String::new();
            std::io::stdin().read_to_string(&mut request)?;
            let contract = cli::adopt_command(&store, &key, &request).await?;
            println!("{}", serde_json::to_string(&contract)?);
        }
        Command::Select { store, key } => {
            let mut request = String::new();
            std::io::stdin().read_to_string(&mut request)?;
            println!("{}", cli::select_command(&store, &key, &request).await?);
        }
        Command::Migrate { store, key } => {
            let mut request = String::new();
            std::io::stdin().read_to_string(&mut request)?;
            let migrated = cli::migrate_command(&store, &key, &request).await?;
            println!("{}", serde_json::to_string(&migrated)?);
        }
        Command::Acceptances { store } => {
            let acceptances = cli::acceptances_command(&store).await?;
            println!("{}", serde_json::to_string(&acceptances)?);
        }
        Command::Incumbent { store } => {
            let incumbent = cli::incumbent_command(&store).await?;
            println!("{}", serde_json::to_string(&incumbent)?);
        }
        Command::Live { store, selection } => {
            println!("{}", cli::live_command(&store, selection).await?);
        }
        Command::Contract { store, id } => {
            let contract = cli::contract_command(&store, &id).await?;
            println!("{}", serde_json::to_string(&contract)?);
        }
        Command::Digest { domain } => {
            let mut value = String::new();
            std::io::stdin().read_to_string(&mut value)?;
            println!("{}", cli::digest_command(&domain, &value)?);
        }
    }
    Ok(())
}
