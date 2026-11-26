{{- define "infra.labels" -}}
app.kubernetes.io/part-of: opspilot
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "infra.pgUrl" -}}
postgresql://{{ .Values.postgres.user }}:{{ .Values.postgres.password }}@opspilot-postgres:5432
{{- end -}}
