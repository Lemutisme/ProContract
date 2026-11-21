use super::*;

fn id(n: u64) -> AcceptanceId {
    AcceptanceId(Digest::of(&n.to_le_bytes()))
}

fn record(n: u64, task: &str, parents: &[u64]) -> AcceptanceRecord {
    AcceptanceRecord {
        id: id(n),
        contract_id: ContractId(task.to_owned()),
        revision: 0,
        // Deliberately identical content: identity must remain record-specific.
        subject_hash: Digest::of(b"same artifact"),
        judgment_hash: Digest::of(&n.to_le_bytes()),
        supports: parents.iter().map(|n| id(*n)).collect(),
    }
}

#[test]
fn identical_artifacts_do_not_share_dependents_or_choose_fallback() {
    let mut graph = AcceptanceGraph::default();
    for r in [
        record(0, "base", &[]),
        record(1, "derived", &[0]),
        record(2, "base", &[]),
    ] {
        graph.insert(r).unwrap();
    }
    assert_eq!(
        graph.affected(&BTreeSet::from([id(0)])).unwrap(),
        BTreeSet::from([id(0), id(1)])
    );
    assert_eq!(
        graph.affected(&BTreeSet::from([id(2)])).unwrap(),
        BTreeSet::from([id(2)])
    );
    assert_eq!(
        graph.records_for(&ContractId("base".into())),
        BTreeSet::from([id(0), id(2)])
    );
}

#[test]
fn invalidation_traverses_historical_records_independent_of_current_liveness() {
    let mut graph = AcceptanceGraph::default();
    for r in [
        record(0, "a", &[]),
        record(1, "b", &[0]),
        record(2, "c", &[1]),
        record(3, "d", &[]),
    ] {
        graph.insert(r).unwrap();
    }
    let already_inactive = graph.affected(&BTreeSet::from([id(1)])).unwrap();
    assert_eq!(already_inactive, BTreeSet::from([id(1), id(2)]));
    // The second invalidation must still walk through record 1.
    assert_eq!(
        graph.affected(&BTreeSet::from([id(0)])).unwrap(),
        BTreeSet::from([id(0), id(1), id(2)])
    );
}

#[test]
fn invalid_insertion_is_atomic_and_cannot_rewrite_an_existing_record() {
    let mut graph = AcceptanceGraph::default();
    graph.insert(record(0, "a", &[])).unwrap();
    let original = graph.clone();
    assert_eq!(
        graph.insert(record(1, "b", &[0, 2])),
        Err(RecordGraphError::UnknownRecord(id(2)))
    );
    assert_eq!(graph, original);
    assert_eq!(
        graph.insert(record(0, "changed", &[])),
        Err(RecordGraphError::DuplicateRecord(id(0)))
    );
    assert_eq!(graph, original);
    assert_eq!(
        graph.insert(record(1, "cycle", &[1])),
        Err(RecordGraphError::UnknownRecord(id(1)))
    );
    assert_eq!(graph, original);
}

#[test]
fn closure_matches_independent_forward_fixed_point_on_generated_histories() {
    let mut random = 137_u64;
    for _ in 0..256 {
        let mut graph = AcceptanceGraph::default();
        let mut history = Vec::new();
        for n in 0..24 {
            let mut parents = Vec::new();
            for parent in 0..n {
                random = random.wrapping_mul(6364136223846793005).wrapping_add(1);
                if random >> 60 == 0 {
                    parents.push(parent);
                }
            }
            let r = record(n, "task", &parents);
            history.push(r.clone());
            graph.insert(r).unwrap();
        }
        for seed in 0..24 {
            let mut expected = BTreeSet::from([id(seed)]);
            loop {
                // Declarative forward scan, independent of the production reverse
                // index and work-list traversal.
                let next: BTreeSet<_> = history
                    .iter()
                    .filter(|r| expected.contains(&r.id) || !r.supports.is_disjoint(&expected))
                    .map(|r| r.id)
                    .collect();
                if next == expected {
                    break;
                }
                expected = next;
            }
            assert_eq!(
                graph.affected(&BTreeSet::from([id(seed)])).unwrap(),
                expected
            );
        }
    }
}

#[test]
fn empty_and_unknown_seeds_have_distinct_meaning() {
    let graph = AcceptanceGraph::default();
    assert_eq!(graph.affected(&BTreeSet::new()).unwrap(), BTreeSet::new());
    assert_eq!(
        graph.affected(&BTreeSet::from([id(0)])),
        Err(RecordGraphError::UnknownRecord(id(0)))
    );
}
