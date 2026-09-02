# PPO parity fixtures

Colab의 legacy Stable-Baselines3 환경에서 다음 명령을 실행한다.

```bash
python scripts/export_sb3_parity_fixture.py --controller all --output-dir tests/fixtures
```

생성되는 `ppo_parity_170k.json`과 `ppo_parity_200k.json`만 저장소로 가져온다. 파일에는 raw observation, VecNormalize 결과, deterministic action, logits와 버전/checksum 메타데이터만 들어가며 모델 가중치는 들어가지 않는다.

fixture가 없을 때 로컬 parity 테스트는 이유를 표시하고 skip한다. fixture가 들어오면 수동 로더의 정규화, logits, action을 SB3 결과와 비교한다.
