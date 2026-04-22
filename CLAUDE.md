# Printer 프로젝트 지침

## 빌드 규칙

코드를 수정한 뒤에는 **항상** 컨테이너를 재빌드해서 변경을 반영해야 한다. 파일만 수정하고 끝내지 말 것.

- `fe/` 수정 시: `docker compose up -d --build fe`
- `be/` 수정 시: `docker compose up -d --build be`
- 양쪽 모두 수정 시: `docker compose up -d --build`

이유: `fe`는 nginx 이미지에 정적 파일을 `COPY`하는 구조이고, `be`도 소스를 이미지에 `COPY`해서 실행하므로, 호스트 파일 수정만으로는 실행 중인 컨테이너에 반영되지 않는다.

빌드 후에는 `docker compose ps`로 컨테이너가 정상 기동했는지, 그리고 필요하면 `curl`로 실제 변경된 파일이 서빙되는지 확인까지 한 뒤에 "완료"라고 보고한다.
