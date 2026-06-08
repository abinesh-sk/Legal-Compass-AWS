const awsConfig = {
  Auth: {
    Cognito: {
      identityPoolId: 'us-east-1:0003c99f-d666-4bf2-a726-29b40b387446',
      region: 'us-east-1',
      allowGuestAccess: true
    }
  },
  API: {
    REST: {
      LegalAidAPI: {
        endpoint: 'https://6que5dlvtc.execute-api.us-east-1.amazonaws.com/prod',
        region: 'us-east-1'
      }
    }
  }
};

export default awsConfig;