window.MAD_DEMO_DATA = {
  "stop_reason": "verifier_gate_passed",
  "verdict": {
    "passed": true,
    "score": 0.78,
    "summary": "event_f1=0.78，阈值 0.7 -> 通过",
    "details": {
      "passed": true,
      "score": 0.78,
      "summary": "event_f1=0.78，阈值 0.7 -> 通过",
      "details": {
        "metric": "event_f1",
        "value": 0.78,
        "threshold": 0.7
      }
    }
  },
  "best_messages": [
    {
      "id": "3d4dc8bee9a3",
      "tag": "CLAIM",
      "body": "用 2 秒滑窗 LightGBM（IMU 频带能量 + 咀嚼周期性）输出进食帧，再以最短 30 秒约束拼接成段。",
      "author": "Explorer",
      "round_no": 1,
      "parent_id": null,
      "created_at": "2026-10-01T12:49:56.196038+00:00",
      "metadata": {},
      "status": "open"
    },
    {
      "id": "4bd011bd957c",
      "tag": "BASELINE_RESULT",
      "body": "规则基线，20 名受试者 × 3 天：event-F1@30s = 0.61，FP/day = 4.2。Seed 42。",
      "author": "Experimenter",
      "round_no": 1,
      "parent_id": null,
      "created_at": "2026-10-01T12:49:56.196545+00:00",
      "metadata": {
        "metrics": {
          "event_f1": 0.61,
          "fp_per_day": 4.2
        }
      },
      "status": "open"
    },
    {
      "id": "a75b27947210",
      "tag": "EXPERIMENT_RESULT",
      "body": "LightGBM+HMM（已应用半马尔可夫修复）：event-F1@30s = 0.78，onset MAE = 22 秒，FP/day = 1.9。受试者不重叠。Seed 42。",
      "author": "Experimenter",
      "round_no": 2,
      "parent_id": null,
      "created_at": "2026-10-01T12:49:56.197501+00:00",
      "metadata": {
        "metrics": {
          "event_f1": 0.78,
          "fp_per_day": 1.9
        }
      },
      "status": "open"
    }
  ],
  "open_questions": [],
  "dead_ends": [
    {
      "id": "f28cc52f1b87",
      "tag": "COUNTEREXAMPLE",
      "body": "嚼口香糖和说话都产生 1-2 Hz 下颌能量；仅凭 IMU 该主张无法把它们与进食分开，见场景 gum_talk_03。",
      "author": "Skeptic",
      "round_no": 1,
      "parent_id": "3d4dc8bee9a3",
      "created_at": "2026-10-01T12:49:56.196232+00:00",
      "metadata": {},
      "status": "open"
    },
    {
      "id": "aa3cd1bdf027",
      "tag": "COUNTEREXAMPLE",
      "body": "硬性 30 秒最短时长会把相隔 20 秒的两次加餐并成一段、推高时长 MAE；没有间隙容忍模型就不要用硬闸门。",
      "author": "Skeptic",
      "round_no": 2,
      "parent_id": "3d4dc8bee9a3",
      "created_at": "2026-10-01T12:49:56.197118+00:00",
      "metadata": {},
      "status": "open"
    },
    {
      "id": "3a8d27c65c18",
      "tag": "DEAD_END",
      "body": "尝试在候选边界上用成对堆式优先级 —— 4 次尝试仍无法给出合并代价上界，放弃。",
      "author": "Modeler",
      "round_no": 2,
      "parent_id": null,
      "created_at": "2026-10-01T12:49:56.197293+00:00",
      "metadata": {},
      "status": "open"
    }
  ],
  "rounds": [
    {
      "round_no": 1,
      "posts": [
        {
          "id": "3d4dc8bee9a3",
          "tag": "CLAIM",
          "body": "用 2 秒滑窗 LightGBM（IMU 频带能量 + 咀嚼周期性）输出进食帧，再以最短 30 秒约束拼接成段。",
          "author": "Explorer",
          "round_no": 1,
          "parent_id": null,
          "created_at": "2026-10-01T12:49:56.196038+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "f28cc52f1b87",
          "tag": "COUNTEREXAMPLE",
          "body": "嚼口香糖和说话都产生 1-2 Hz 下颌能量；仅凭 IMU 该主张无法把它们与进食分开，见场景 gum_talk_03。",
          "author": "Skeptic",
          "round_no": 1,
          "parent_id": "3d4dc8bee9a3",
          "created_at": "2026-10-01T12:49:56.196232+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "4a76c4d2a2e2",
          "tag": "FIX",
          "body": "用半马尔可夫先验替换硬性 30 秒最短时长：仅当加餐间隔 < 12 秒且同手 IMU 连续时才允许合并。",
          "author": "Modeler",
          "round_no": 1,
          "parent_id": "3d4dc8bee9a3",
          "created_at": "2026-10-01T12:49:56.196402+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "4bd011bd957c",
          "tag": "BASELINE_RESULT",
          "body": "规则基线，20 名受试者 × 3 天：event-F1@30s = 0.61，FP/day = 4.2。Seed 42。",
          "author": "Experimenter",
          "round_no": 1,
          "parent_id": null,
          "created_at": "2026-10-01T12:49:56.196545+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "1b836b4a478a",
          "tag": "VERDICT",
          "body": "event_f1=0.61，阈值 0.7 -> 未通过",
          "author": "verifier",
          "round_no": 1,
          "parent_id": null,
          "created_at": "2026-10-01T12:49:56.196766+00:00",
          "metadata": {
            "passed": false,
            "score": 0.61,
            "summary": "event_f1=0.61，阈值 0.7 -> 未通过",
            "details": {
              "metric": "event_f1",
              "value": 0.61,
              "threshold": 0.7
            }
          },
          "status": "open"
        }
      ],
      "uncontested": [],
      "verified": {
        "passed": false,
        "score": 0.61,
        "summary": "event_f1=0.61，阈值 0.7 -> 未通过",
        "details": {
          "metric": "event_f1",
          "value": 0.61,
          "threshold": 0.7
        }
      },
      "stop_reason": null
    },
    {
      "round_no": 2,
      "posts": [
        {
          "id": "7a66c4ba3bef",
          "tag": "PROPOSAL",
          "body": "增加餐后 PPG 确认通道（onset 后 10-20 分钟心率上升），压制说话造成的误报。",
          "author": "Explorer",
          "round_no": 2,
          "parent_id": null,
          "created_at": "2026-10-01T12:49:56.196937+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "aa3cd1bdf027",
          "tag": "COUNTEREXAMPLE",
          "body": "硬性 30 秒最短时长会把相隔 20 秒的两次加餐并成一段、推高时长 MAE；没有间隙容忍模型就不要用硬闸门。",
          "author": "Skeptic",
          "round_no": 2,
          "parent_id": "3d4dc8bee9a3",
          "created_at": "2026-10-01T12:49:56.197118+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "3a8d27c65c18",
          "tag": "DEAD_END",
          "body": "尝试在候选边界上用成对堆式优先级 —— 4 次尝试仍无法给出合并代价上界，放弃。",
          "author": "Modeler",
          "round_no": 2,
          "parent_id": null,
          "created_at": "2026-10-01T12:49:56.197293+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "a75b27947210",
          "tag": "EXPERIMENT_RESULT",
          "body": "LightGBM+HMM（已应用半马尔可夫修复）：event-F1@30s = 0.78，onset MAE = 22 秒，FP/day = 1.9。受试者不重叠。Seed 42。",
          "author": "Experimenter",
          "round_no": 2,
          "parent_id": null,
          "created_at": "2026-10-01T12:49:56.197501+00:00",
          "metadata": {},
          "status": "open"
        },
        {
          "id": "4c60ed7a4864",
          "tag": "VERDICT",
          "body": "event_f1=0.78，阈值 0.7 -> 通过",
          "author": "verifier",
          "round_no": 2,
          "parent_id": null,
          "created_at": "2026-10-01T12:49:56.197756+00:00",
          "metadata": {
            "passed": true,
            "score": 0.78,
            "summary": "event_f1=0.78，阈值 0.7 -> 通过",
            "details": {
              "metric": "event_f1",
              "value": 0.78,
              "threshold": 0.7
            }
          },
          "status": "open"
        }
      ],
      "uncontested": [],
      "verified": {
        "passed": true,
        "score": 0.78,
        "summary": "event_f1=0.78，阈值 0.7 -> 通过",
        "details": {
          "metric": "event_f1",
          "value": 0.78,
          "threshold": 0.7
        }
      },
      "stop_reason": "verifier_gate_passed"
    }
  ],
  "task": "从可穿戴 IMU+PPG 识别进食时段 [onset, offset]。优化 event 级 F1（±30s），压低 FP/day。不做营养学声明。"
};
