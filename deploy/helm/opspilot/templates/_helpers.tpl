{{- define "opspilot.labels" -}}
app.kubernetes.io/part-of: opspilot
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "opspilot.pythonImage" -}}
{{ .Values.image.python.repository }}:{{ .Values.image.python.tag }}
{{- end -}}
