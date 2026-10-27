// Synthetic fixture copied from sudo-sane/coverletterbot c978f1198d35b147510757a55065d05c034bf26d.
// Original: services/assessment-service/testdata/engine/v1/goldens/scripted/profile-sales_b2b-partial.json
// Source ranges and hashes checked while generating this file; test recomputes them.
export const engineGolden = {
  "body": {
    "candidate_id": "6962ced7-2f4c-57ce-8fed-7adfaa992324",
    "evidence": [
      {
        "assisted": false,
        "byte_from": 797,
        "byte_to": 1109,
        "channel": "interview",
        "counterpart_response_turn_ids": [
          5
        ],
        "criterion_id": "A07",
        "episode_id": "ep_156a53192b12229c",
        "evidence_id": "ev_48066bcefdb6688c655f",
        "evidence_type": "observed_case_behavior",
        "facet": "specific_next_step",
        "generic_statement": false,
        "grounding_status": "verbatim",
        "number_uncertain": false,
        "prompting": "unassisted",
        "prompting_subtype": null,
        "quote_sha256": "f4c8b3ff2be57069eb7a0d429ed836b548a6fbbd967106c7c070329fbc0db0fe",
        "source_id": "interview-sales_b2b-partial",
        "source_kind": "interview_transcript",
        "span_role": "candidate",
        "star": {
          "event": false,
          "own_role": false,
          "period": false,
          "quantity": false
        },
        "turn_ids": [
          4
        ],
        "word_from": 52,
        "word_to": 75
      },
      {
        "assisted": false,
        "byte_from": 1345,
        "byte_to": 1571,
        "channel": "interview",
        "counterpart_response_turn_ids": [],
        "criterion_id": "A04",
        "episode_id": "ep_109c1fb7aa3338e9",
        "evidence_id": "ev_6dd1a73b5c7aac811348",
        "evidence_type": "interview_self_report",
        "facet": "business_problem_questions",
        "generic_statement": false,
        "grounding_status": "verbatim",
        "number_uncertain": false,
        "prompting": "unassisted",
        "prompting_subtype": null,
        "quote_sha256": "409e7d108416311c2cd6c0d521b3e0b497c5089c048f769fb8d6e2b9a18837a0",
        "source_id": "interview-sales_b2b-partial",
        "source_kind": "interview_transcript",
        "span_role": "candidate",
        "star": {
          "event": true,
          "own_role": true,
          "period": true,
          "quantity": false
        },
        "turn_ids": [
          7
        ],
        "word_from": 91,
        "word_to": 109
      },
      {
        "assisted": false,
        "byte_from": 234,
        "byte_to": 685,
        "channel": "interview",
        "counterpart_response_turn_ids": [
          3
        ],
        "criterion_id": "A10",
        "episode_id": "ep_156a53192b12229c",
        "evidence_id": "ev_d6615cd7195607c9f6e9",
        "evidence_type": "observed_case_behavior",
        "facet": "knowledge",
        "generic_statement": false,
        "grounding_status": "verbatim",
        "number_uncertain": false,
        "prompting": "unassisted",
        "prompting_subtype": null,
        "quote_sha256": "7550da1e808c3d1dd8547dfc82d9bab3756b5d2eaaeba34e0891e5fd0da889ac",
        "source_id": "interview-sales_b2b-partial",
        "source_kind": "interview_transcript",
        "span_role": "candidate",
        "star": {
          "event": false,
          "own_role": false,
          "period": false,
          "quantity": false
        },
        "turn_ids": [
          2
        ],
        "word_from": 15,
        "word_to": 46
      }
    ],
    "families": [
      {
        "axes": [
          {
            "anchor_refs": [],
            "axis_id": "C01",
            "citations": [],
            "criterion_ids": [
              "A01"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_id": "C04",
            "citations": [],
            "criterion_ids": [
              "A09"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_id": "C05",
            "citations": [],
            "criterion_ids": [
              "A11.1",
              "A11.2"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "aggregation_rule_not_approved"
            ],
            "state": "unknown"
          },
          {
            "anchor_refs": [],
            "axis_id": "S-SAL-01",
            "citations": [],
            "criterion_ids": [
              "A02",
              "A03"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "axis_not_applicable"
            ],
            "state": "not_applicable"
          },
          {
            "anchor_refs": [
              {
                "action_id": "A04.full_cycle_b2b.L2.a1",
                "anchor_revision": "anchors-2026-10-v2",
                "block_hash": "79e29bec9a0b63387fe8c79507b055a19f907f8d5d4194cbadc0a6a04c1ba45d",
                "concern": false,
                "criterion_id": "A04",
                "level": 2,
                "manifestation": "full_cycle_b2b"
              }
            ],
            "axis_id": "S-SAL-02",
            "citations": [
              {
                "basis": "narrated",
                "byte_from": 1345,
                "byte_to": 1571,
                "evidence_id": "ev_6dd1a73b5c7aac811348",
                "quote_sha256": "409e7d108416311c2cd6c0d521b3e0b497c5089c048f769fb8d6e2b9a18837a0",
                "source_id": "interview-sales_b2b-partial",
                "unit_id": "eg_109c1fb7aa3338e9:narrated",
                "unit_level": 2
              }
            ],
            "criterion_ids": [
              "A04"
            ],
            "failed_processing": false,
            "level": 2,
            "reason_codes": [],
            "state": "supported"
          },
          {
            "anchor_refs": [],
            "axis_id": "S-SAL-03",
            "citations": [],
            "criterion_ids": [
              "A05"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [],
            "state": "unknown"
          },
          {
            "anchor_refs": [],
            "axis_id": "S-SAL-04",
            "citations": [],
            "criterion_ids": [
              "A06.1",
              "A06.2"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "aggregation_rule_not_approved"
            ],
            "state": "unknown"
          },
          {
            "anchor_refs": [
              {
                "action_id": "A07.full_cycle_b2b.L4.a1",
                "anchor_revision": "anchors-2026-10-v2",
                "block_hash": "f2bd6140cb7b3f6671689005576a86123d63cc366902f406388f83e4b8fb5c49",
                "concern": false,
                "criterion_id": "A07",
                "level": 4,
                "manifestation": "full_cycle_b2b"
              }
            ],
            "axis_id": "S-SAL-05",
            "citations": [
              {
                "basis": "observed",
                "byte_from": 797,
                "byte_to": 1109,
                "evidence_id": "ev_48066bcefdb6688c655f",
                "quote_sha256": "f4c8b3ff2be57069eb7a0d429ed836b548a6fbbd967106c7c070329fbc0db0fe",
                "source_id": "interview-sales_b2b-partial",
                "unit_id": "ep_156a53192b12229c:observed",
                "unit_level": 4
              }
            ],
            "criterion_ids": [
              "A07"
            ],
            "failed_processing": false,
            "level": 4,
            "reason_codes": [],
            "state": "supported"
          },
          {
            "anchor_refs": [],
            "axis_id": "S-SAL-06",
            "citations": [],
            "criterion_ids": [
              "A08"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "state": "not_applicable"
          },
          {
            "anchor_refs": [
              {
                "action_id": "A10.full_cycle_b2b.L4.a1",
                "anchor_revision": "anchors-2026-10-v2",
                "block_hash": "0266d7f2ef04756438f609e1bfc5f39d99ea8a1389f242319f823cef0d384ade",
                "concern": false,
                "criterion_id": "A10",
                "level": 4,
                "manifestation": "full_cycle_b2b"
              }
            ],
            "axis_id": "S-SAL-07",
            "citations": [
              {
                "basis": "observed",
                "byte_from": 234,
                "byte_to": 685,
                "evidence_id": "ev_d6615cd7195607c9f6e9",
                "quote_sha256": "7550da1e808c3d1dd8547dfc82d9bab3756b5d2eaaeba34e0891e5fd0da889ac",
                "source_id": "interview-sales_b2b-partial",
                "unit_id": "ep_156a53192b12229c:observed",
                "unit_level": 4
              }
            ],
            "criterion_ids": [
              "A10"
            ],
            "failed_processing": false,
            "level": 4,
            "reason_codes": [],
            "state": "supported"
          },
          {
            "anchor_refs": [],
            "axis_id": "S-SAL-08",
            "citations": [],
            "criterion_ids": [
              "A12"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_id": "S-SAL-09",
            "citations": [],
            "criterion_ids": [
              "A13"
            ],
            "failed_processing": false,
            "level": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "state": "not_applicable"
          }
        ],
        "criteria": [
          {
            "anchor_refs": [
              {
                "action_id": "A04.full_cycle_b2b.L2.a1",
                "anchor_revision": "anchors-2026-10-v2",
                "block_hash": "79e29bec9a0b63387fe8c79507b055a19f907f8d5d4194cbadc0a6a04c1ba45d",
                "concern": false,
                "criterion_id": "A04",
                "level": 2,
                "manifestation": "full_cycle_b2b"
              }
            ],
            "axis_ids": [
              "S-SAL-02"
            ],
            "basis": "narrated",
            "citations": [
              {
                "basis": "narrated",
                "byte_from": 1345,
                "byte_to": 1571,
                "evidence_id": "ev_6dd1a73b5c7aac811348",
                "quote_sha256": "409e7d108416311c2cd6c0d521b3e0b497c5089c048f769fb8d6e2b9a18837a0",
                "source_id": "interview-sales_b2b-partial",
                "unit_id": "eg_109c1fb7aa3338e9:narrated",
                "unit_level": 2
              }
            ],
            "class_levels": {
              "narrated": 2
            },
            "criterion_id": "A04",
            "excluded": {},
            "failed_processing": false,
            "flags": [
              "single_episode"
            ],
            "level": 2,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [],
            "role": "primary",
            "state": "supported"
          },
          {
            "anchor_refs": [
              {
                "action_id": "A10.full_cycle_b2b.L4.a1",
                "anchor_revision": "anchors-2026-10-v2",
                "block_hash": "0266d7f2ef04756438f609e1bfc5f39d99ea8a1389f242319f823cef0d384ade",
                "concern": false,
                "criterion_id": "A10",
                "level": 4,
                "manifestation": "full_cycle_b2b"
              }
            ],
            "axis_ids": [
              "S-SAL-07"
            ],
            "basis": "observed",
            "citations": [
              {
                "basis": "observed",
                "byte_from": 234,
                "byte_to": 685,
                "evidence_id": "ev_d6615cd7195607c9f6e9",
                "quote_sha256": "7550da1e808c3d1dd8547dfc82d9bab3756b5d2eaaeba34e0891e5fd0da889ac",
                "source_id": "interview-sales_b2b-partial",
                "unit_id": "ep_156a53192b12229c:observed",
                "unit_level": 4
              }
            ],
            "class_levels": {
              "observed": 4
            },
            "criterion_id": "A10",
            "excluded": {},
            "failed_processing": false,
            "flags": [
              "single_episode"
            ],
            "level": 4,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [],
            "role": "primary",
            "state": "supported"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "C05"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A11.1",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": "not_asked",
            "reason_codes": [],
            "role": "primary",
            "state": "unknown"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-04"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A06.1",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": "not_asked",
            "reason_codes": [],
            "role": "primary",
            "state": "unknown"
          },
          {
            "anchor_refs": [
              {
                "action_id": "A07.full_cycle_b2b.L4.a1",
                "anchor_revision": "anchors-2026-10-v2",
                "block_hash": "f2bd6140cb7b3f6671689005576a86123d63cc366902f406388f83e4b8fb5c49",
                "concern": false,
                "criterion_id": "A07",
                "level": 4,
                "manifestation": "full_cycle_b2b"
              }
            ],
            "axis_ids": [
              "S-SAL-05"
            ],
            "basis": "observed",
            "citations": [
              {
                "basis": "observed",
                "byte_from": 797,
                "byte_to": 1109,
                "evidence_id": "ev_48066bcefdb6688c655f",
                "quote_sha256": "f4c8b3ff2be57069eb7a0d429ed836b548a6fbbd967106c7c070329fbc0db0fe",
                "source_id": "interview-sales_b2b-partial",
                "unit_id": "ep_156a53192b12229c:observed",
                "unit_level": 4
              }
            ],
            "class_levels": {
              "observed": 4
            },
            "criterion_id": "A07",
            "excluded": {},
            "failed_processing": false,
            "flags": [
              "single_episode"
            ],
            "level": 4,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [],
            "role": "primary",
            "state": "supported"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "C01"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A01",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-01"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A02",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-01"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A03",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-03"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A05",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": "not_asked",
            "reason_codes": [],
            "role": "display",
            "state": "unknown"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-04"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A06.2",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": "stage_not_reached",
            "reason_codes": [],
            "role": "display",
            "state": "unknown"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-06"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A08",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "C04"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A09",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "C05"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A11.2",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-08"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A12",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          },
          {
            "anchor_refs": [],
            "axis_ids": [
              "S-SAL-09"
            ],
            "basis": null,
            "citations": [],
            "class_levels": {},
            "criterion_id": "A13",
            "excluded": {},
            "failed_processing": false,
            "flags": [],
            "level": null,
            "manifestation": "full_cycle_b2b",
            "missing_reason": null,
            "reason_codes": [
              "outside_anchor_scope"
            ],
            "role": "display",
            "state": "not_applicable"
          }
        ],
        "family_id": "sales_b2b",
        "manifestation": "full_cycle_b2b",
        "rule_ref": "rating-core-v2+comb-v1-b142",
        "template_id": "T1_b2b_full_cycle_ic"
      }
    ]
  },
  "created_at": "2026-10-19T09:00:00Z",
  "document_id": "e983f3f1-c224-5426-ada3-2bfb5f9a5d8d",
  "document_sha256": "2948050ea7f20cdd941a83c0ed2ae3e7e197b29810f30a6247e3ffa6bac0f8aa",
  "engine_contract": "engine-v1",
  "format": "candidate_profile/1",
  "inputs": [
    {
      "class": "sources",
      "sha256": "ed9db1df05781c991254f72c7f6276725b9f453c02fda0440791421414950c54"
    },
    {
      "class": "context",
      "sha256": "bf84dfbfa2007c2da7992cdc33f51f1866d3715f3f47dc30e97f10a5eb6c4f56"
    },
    {
      "class": "method",
      "sha256": "2bbbb07d2c243ee016796ccaa76d7bc662061902b605b01f8946a3fb9fdecc51"
    },
    {
      "class": "model",
      "sha256": "ec29f8931b1fae2d524220486453e234ac1b43be23dbe5ac906734660f919431"
    }
  ],
  "limitations": [
    {
      "code": "anchor_blocks_draft",
      "subject": "anchors-2026-10-v2"
    },
    {
      "code": "cv_corroboration_only",
      "subject": "E9"
    },
    {
      "code": "deferred_anchor_axis",
      "subject": "C02"
    },
    {
      "code": "deferred_anchor_axis",
      "subject": "C06"
    },
    {
      "code": "deferred_anchor_axis",
      "subject": "C07"
    },
    {
      "code": "deferred_anchor_axis",
      "subject": "C08"
    },
    {
      "code": "deferred_anchor_axis",
      "subject": "S-CS-02"
    },
    {
      "code": "level5_corroboration_rule",
      "subject": "comb-v1-b142"
    },
    {
      "code": "model_scripted",
      "subject": "model_script/1"
    },
    {
      "code": "multi_criterion_axis_unrated",
      "subject": "C05"
    },
    {
      "code": "multi_criterion_axis_unrated",
      "subject": "S-SAL-04"
    },
    {
      "code": "privacy_asserted_by_caller",
      "subject": "synthetic_raw"
    }
  ],
  "method": {
    "files": [
      {
        "content_hash": "sha256:e66428b073ae5f17f4d925643a19cd06419816e77791b8f6ee804ed8beec3797",
        "file_sha256": "82561a0acde6c626264dbb850d71e545639df963f395aef92513820592cd9133",
        "path": "templates-method/catalog/mvp-catalog-2026-10-v2.json",
        "revision": "mvp-catalog-2026-10-v2",
        "status": "approved"
      },
      {
        "content_hash": "sha256:481f47a3339f35b0b05d7677c696e597a97b5fe7a45ef8a8de3559df049f9e61",
        "file_sha256": "add5e9e1369214148a433bcc35659699ba37b064150b939fd56d9cb31ec91ded",
        "path": "templates-method/anchors/anchors-2026-10-v2.json",
        "revision": "anchors-2026-10-v2",
        "status": "approved"
      },
      {
        "content_hash": "sha256:77fb2269b1c5a235d3ef2f57be51eb39abb4183996b78093cf5320dc7acb071f",
        "file_sha256": "59c0e09ab181716aaf953829a0bf36d5f21c44f05172c2349427c860b29e567b",
        "path": "templates-method/catalog/criteria-2026-10-v1.json",
        "revision": "criteria-2026-10-v1",
        "status": "approved"
      },
      {
        "content_hash": "sha256:b53c6b6305bfdc55a04f74cf96223d6d34da7608614d8210523a1175be6b9f15",
        "file_sha256": "9b07ef3f8d0c620d52fb958a9a303fa790f175f9ddb301fe2cd6b27102bc1f8a",
        "path": "templates-method/catalog/crosswalk-v01-v02.json",
        "revision": "crosswalk-v01-v02-2026-10-v1",
        "status": "approved"
      },
      {
        "content_hash": "sha256:429157f77593b96f861104d0b40f772669ed70fef003d5a7b352bbf2ed2501d6",
        "file_sha256": "79e9f204bae8f4ad969d95596c020ff21d9be531ac9f0a3f326bbf6deb956c35",
        "path": "templates-method/catalog/axes-v0.2.json",
        "revision": "axes-v0.2-2026-10-v1",
        "status": "approved"
      },
      {
        "content_hash": "sha256:e3fe1e49be2871f1302b4d06fb2f0e16b3b066ad3a281fa598dab4608c443199",
        "file_sha256": "fe272ae520cbad08ad4d9e825f30be4c50facf3b2456659d3db1fd748d390227",
        "path": "templates-method/admissibility.json",
        "revision": "admissibility-2026-09-24a",
        "status": "approved"
      },
      {
        "content_hash": "sha256:dbdb9ee91fbc823b304df9720138021d4a8b668a763897ecb1d21e234544444c",
        "file_sha256": "08c0758e1001c7777d88755c2a75ace07772ba7c4f07a35c30b5dfd7e415e563",
        "path": "templates-method/policy/scoring-policy-v1.json",
        "revision": "policy-v1",
        "status": "approved"
      },
      {
        "content_hash": "sha256:682b3d5b8c7fb3a165930cae4acace79688e983a306cab60aa6d4a7084b8f092",
        "file_sha256": "aecc8d71387b7ee063e182ec3a299d6d1df646ede84c9d01710e544a16579734",
        "path": "templates-method/policy/protected-terms.ru.json",
        "revision": "protected-terms-ru-2026-10-01a",
        "status": "approved"
      },
      {
        "content_hash": "sha256:9868375a63f06b44ac442fe18d46024b842bb9ec434a4b0e2999fdd5eb5eba10",
        "file_sha256": "677fcc6383ca33ba0d09032d9f80f12d8acf5b2bc648902c0379a1c136c00828",
        "path": "templates-method/roles/T1_b2b_full_cycle_ic.json",
        "revision": "T1_b2b_full_cycle_ic@0.1.0",
        "status": "approved"
      },
      {
        "content_hash": "sha256:8590a2aad44e86462cb9ed75906033267aecf000576cda9b19acb6bd49450457",
        "file_sha256": "41a9cb7a593c3499b53b75dedd9cdd48e89869b10b2b22c5c147f5ed04cb4cad",
        "path": "templates-method/roles/T2_b2b_presales_leadgen.json",
        "revision": "T2_b2b_presales_leadgen@0.1.0",
        "status": "approved"
      },
      {
        "content_hash": "sha256:7116d9c2ea81e0a93a9ff7463d41c5cc74724743a81b99d627b86329d6cee87e",
        "file_sha256": "da1422878b025561d8dbe0af552d4eab101e6ca641b60b097b1c78770e676ea9",
        "path": "templates-method/roles/T3_b2c_inbound.json",
        "revision": "T3_b2c_inbound@0.1.0",
        "status": "approved"
      },
      {
        "content_hash": "sha256:c5a474282c70ad83f6637da0af89f42aece72bf0d16caa00e17cecc2adc005fb",
        "file_sha256": "028e5d962ca936882ef2cba51d4dedb196742ac9e17a7d0837f1c0155cf1eb19",
        "path": "templates-method/roles/T4_head_of_sales.json",
        "revision": "T4_head_of_sales@0.1.0",
        "status": "approved"
      }
    ],
    "prompts": {
      "agent.criterion_assessor": "criterion_assessor-2026-10-11a",
      "agent.evidence_extractor": "evidence_extractor-2026-10-19a",
      "agent.lab_cv_extractor": "lab_cv_extractor-2026-10-19a"
    },
    "rules": {
      "combination": "comb-v1-b142",
      "evidence_path": "evidence-path-v3",
      "evidence_rules": "evidence-rules-2026-09-24a",
      "feedback": "feedback-attribution-v1@1:5eb898f469d0aed31637f27734fb77a9af632b065283f6180344a3911c968a0b",
      "lexfake": "lexfake-v1",
      "ranking": "rank-groups-v2",
      "rating": "rating-core-v2+comb-v1-b142",
      "requirement": "requirement-proposal-v1@1:2d245e5caf267030dbd04c419e50fe86abb5e16ed92e381a3e853ec134c145da"
    },
    "set_sha256": "2bbbb07d2c243ee016796ccaa76d7bc662061902b605b01f8946a3fb9fdecc51"
  },
  "model": {
    "call_count": 6,
    "call_log_sha256": "7b340dfabc50b99847a6b8b56f47962cdbe0b435b11b0342fb2716740617f55f",
    "mode": "scripted",
    "model_family": "scripted",
    "model_id": "scripted",
    "model_version": "model_script/1",
    "provider": "engine",
    "script_sha256": "f5b520ae0bbe7efabb5a84350d5a8ff30d8f494158b1d67ccba00e2a0b73f042"
  },
  "notice": "assistive_not_a_decision",
  "synthetic": true
} as const;

export const canonicalSources = [
  {
    "sourceId": "interview-sales_b2b-partial",
    "kind": "interview_transcript",
    "sha256": "087291c66fc13dff04543e4f0c492826af37e97bd0b1990ebc09199b6b62654a",
    "text": "И: synthetic: true; Учебная задача на сентябрь: вы представляете вымышленную мастерскую. Вводная дана, собеседник вправе отказаться.\nК: Можно оставить ручную сверку: её ведёте вы. Наш учебный модуль сверяет остатки автоматически, но отдельного специалиста для настройки всё равно потребуется выделить; у второго вымышленного решения настройка проще, зато нет проверки расхождений.\nИ: Подтверждаю учебный ответ; продолжайте самостоятельно.\nК: Для проверки вашего критерия — отсутствия расхождений — предлагаю пробную сверку в среду: я готовлю таблицу, вы выбираете склад. Подтвердите, подходит ли такая проверка?\nИ: Подтверждаю учебный ответ; продолжайте самостоятельно.\nИ: Теперь отдельный рассказ об учебном событии; что делали лично вы?\nК: В сентябре я сам разбирал учебную заявку мастерской: выяснил только текущий объём, 20 заказов, а причины задержек не уточнял.\n"
  },
  {
    "sourceId": "cv-sales_b2b-partial",
    "kind": "cv_text",
    "sha256": "fa3fe1967a285149bd9809a367cec395b286de2c5be534afbeaf43dddea911d0",
    "text": "synthetic: true\nУчебный соискатель выполнял задачи в вымышленной мастерской «Листосбор».\nЗа сентябрь обработал 20 учебных обращений.\n"
  }
] as const;

/** Build-time resolved; this is not a browser cryptographic verification claim. */
export const resolvedFixtureExcerpts = [
  {
    "evidenceId": "ev_48066bcefdb6688c655f",
    "sourceId": "interview-sales_b2b-partial",
    "sourceSha256": "087291c66fc13dff04543e4f0c492826af37e97bd0b1990ebc09199b6b62654a",
    "quoteSha256": "f4c8b3ff2be57069eb7a0d429ed836b548a6fbbd967106c7c070329fbc0db0fe",
    "byteFrom": 797,
    "byteTo": 1109,
    "text": "Для проверки вашего критерия — отсутствия расхождений — предлагаю пробную сверку в среду: я готовлю таблицу, вы выбираете склад. Подтвердите, подходит ли такая проверка?"
  },
  {
    "evidenceId": "ev_6dd1a73b5c7aac811348",
    "sourceId": "interview-sales_b2b-partial",
    "sourceSha256": "087291c66fc13dff04543e4f0c492826af37e97bd0b1990ebc09199b6b62654a",
    "quoteSha256": "409e7d108416311c2cd6c0d521b3e0b497c5089c048f769fb8d6e2b9a18837a0",
    "byteFrom": 1345,
    "byteTo": 1571,
    "text": "В сентябре я сам разбирал учебную заявку мастерской: выяснил только текущий объём, 20 заказов, а причины задержек не уточнял."
  },
  {
    "evidenceId": "ev_d6615cd7195607c9f6e9",
    "sourceId": "interview-sales_b2b-partial",
    "sourceSha256": "087291c66fc13dff04543e4f0c492826af37e97bd0b1990ebc09199b6b62654a",
    "quoteSha256": "7550da1e808c3d1dd8547dfc82d9bab3756b5d2eaaeba34e0891e5fd0da889ac",
    "byteFrom": 234,
    "byteTo": 685,
    "text": "Можно оставить ручную сверку: её ведёте вы. Наш учебный модуль сверяет остатки автоматически, но отдельного специалиста для настройки всё равно потребуется выделить; у второго вымышленного решения настройка проще, зато нет проверки расхождений."
  }
] as const;
